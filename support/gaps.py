"""Database side of the self-learning loop (see `dejafix.agents.knowledge` for the Meko side).

As in `support.services`, Meko and Claude are called outside transactions, and every state
change is a conditional UPDATE on the gap's current status, so two runs (or two reviewers)
can't publish or verify the same gap twice.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import Value
from django.db.models.functions import Lower, Replace
from django.utils import timezone

from dejafix.agents.casefile import JSON_CONTENT_TYPE, put_artifact
from dejafix.agents.knowledge import (
    FAILED_STATES,
    INDEXED_STATES,
    LIBRARIAN_AGENT,
    GapCandidate,
    Uploader,
    VerifyResult,
    article_rank,
    compose_article,
    document_state,
    draft_article,
    post_to_presigned_url,
    publish_article,
    scan_gaps,
    verify_gap,
)
from dejafix.agents.memory import format_fix_memory
from dejafix.agents.pipeline import RESOLVER_AGENTS
from dejafix.agents.trace import AgentTrace
from dejafix.llm import LLMError, StructuredLLM
from dejafix.meko import MekoCallRecord, MekoError, MekoToolError
from support.models import AgentRun, KnowledgeGap, Resolution
from support.services import MAX_FAILURE_REASON, ReviewError, SessionFactory, _factory

logger = logging.getLogger("dejafix.support")

MAX_DRAFTS_PER_RUN = 20
# selflearning_verify allows 10 replays a minute per user and counts against the search quota.
MAX_VERIFY_PER_RUN = 10
NO_APPROVED_FIX = "No human-approved fix covers this search yet; a support writer must author the article."
RATE_LIMIT_CODES = ("rate_limit", "rate_limited", "too_many_requests")


def default_uploader() -> Uploader:
    # Looked up at call time so tests (and the admin) can swap the real HTTPS upload.
    return post_to_presigned_url


def _error_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:MAX_FAILURE_REASON]


# --- 1. Import --------------------------------------------------------------------------------


@dataclass
class ImportResult:
    quality_pct: int | None
    created: int = 0
    updated: int = 0
    ignored: int = 0


def import_gaps(*, session_factory: SessionFactory | None = None) -> ImportResult:
    """Pull weak searches by our resolvers from Meko's self-learning log into KnowledgeGap rows."""
    calls: list[MekoCallRecord] = []

    async def scan():
        async with _factory(session_factory)(calls) as root:
            trace = AgentTrace(root, agent=LIBRARIAN_AGENT, ticket_ref=f"gaps-{timezone.now():%Y%m%d-%H%M%S}")
            librarian = await trace.open()
            found = await scan_gaps(librarian, our_agents=set(RESOLVER_AGENTS.values()))
            await trace.record(
                "find_gaps",
                input_text="selflearning add_context",
                output={"quality_pct": found.quality_pct, "gaps": len(found.candidates or []), "ignored": found.ignored},
                reasoning="Weak searches by our resolvers show where shared context is missing.",
            )
            return found

    found = async_to_sync(scan)()
    result = ImportResult(quality_pct=found.quality_pct, ignored=found.ignored)
    candidates = found.candidates or []
    tickets = _tickets_by_conversation([candidate.conversation_id for candidate in candidates])
    with transaction.atomic():
        for candidate in candidates:
            created = _upsert_gap(candidate, tickets.get(candidate.conversation_id))
            result.created += created
            result.updated += not created
    return result


def _tickets_by_conversation(conversation_ids: Sequence[str]) -> dict[str, int]:
    """Map normalized conversation ids to tickets (we store what conversation_create returned)."""
    ids = [conversation_id for conversation_id in conversation_ids if conversation_id]
    runs = (
        AgentRun.objects.annotate(normalized=Lower(Replace("meko_conversation_id", Value("-"), Value(""))))
        .filter(normalized__in=ids)
        .values_list("normalized", "ticket_id")
    )
    return dict(runs)


def _upsert_gap(candidate: GapCandidate, ticket_id: int | None) -> bool:
    gap = KnowledgeGap.objects.select_for_update().filter(query=candidate.query).first()
    if gap is None:
        KnowledgeGap.objects.create(
            query=candidate.query,
            log_id=candidate.log_id,
            agent=candidate.agent_id,
            ticket_id=ticket_id,
            reason=candidate.reason,
            band_before=candidate.band,
            score_before=candidate.best_score,
            times_asked=candidate.count,
        )
        return True
    fields = {"times_asked": candidate.count}
    # The "before" measurement is frozen once a fix is published, so the comparison stays honest.
    if gap.status in KnowledgeGap.EDITABLE_STATES:
        fields.update(
            log_id=candidate.log_id, agent=candidate.agent_id, reason=candidate.reason,
            band_before=candidate.band, score_before=candidate.best_score,
        )
        if ticket_id and not gap.ticket_id:
            fields["ticket_id"] = ticket_id
    KnowledgeGap.objects.filter(pk=gap.pk).update(updated_at=timezone.now(), **fields)
    return False


# --- 2. Draft ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class DraftOutcome:
    gap: KnowledgeGap
    status: str
    tokens: int = 0
    error: str = ""


def approved_fixes_for(gap: KnowledgeGap) -> list[str]:
    """Human-approved fixes behind the gap's ticket, in the curator's memory format."""
    if not gap.ticket_id:
        return []
    resolutions = Resolution.objects.filter(ticket_id=gap.ticket_id, review_status=Resolution.Review.APPROVED).select_related("ticket")
    return [
        format_fix_memory(
            signature=resolution.problem_signature,
            category=resolution.category,
            summary=resolution.fix_summary,
            steps=resolution.fix_steps,
            ticket_ref=resolution.ticket.external_id,
        )
        for resolution in resolutions
    ]


def draft_gap(gap: KnowledgeGap, *, llm: StructuredLLM, session_factory: SessionFactory | None = None) -> DraftOutcome:
    """Draft an article for an open gap. Without a human-approved fix, ask a human author instead."""
    if gap.status != KnowledgeGap.Status.OPEN:
        return DraftOutcome(gap=gap, status=gap.status)
    fixes = approved_fixes_for(gap)
    if not fixes:
        KnowledgeGap.objects.filter(pk=gap.pk, status=KnowledgeGap.Status.OPEN).update(
            status=KnowledgeGap.Status.NEEDS_AUTHOR, draft_reasoning=NO_APPROVED_FIX, updated_at=timezone.now()
        )
        gap.refresh_from_db()
        return DraftOutcome(gap=gap, status=gap.status)

    calls: list[MekoCallRecord] = []

    async def draft():
        async with _factory(session_factory)(calls) as meko:
            return await draft_article(meko, llm, gap_ref=f"gap-{gap.pk}", query=gap.query, approved_fixes=fixes)

    try:
        drafted = async_to_sync(draft)()
    except (MekoError, LLMError) as exc:
        logger.warning("Drafting gap %s failed: %s", gap.pk, _error_text(exc))
        return DraftOutcome(gap=gap, status=gap.status, error=_error_text(exc))

    status = KnowledgeGap.Status.DRAFTED if drafted.covers_query else KnowledgeGap.Status.NEEDS_AUTHOR
    KnowledgeGap.objects.filter(pk=gap.pk, status=KnowledgeGap.Status.OPEN).update(
        status=status,
        draft_title=drafted.title,
        draft_body=drafted.body if drafted.covers_query else "",
        draft_reasoning=drafted.reasoning,
        updated_at=timezone.now(),
    )
    gap.refresh_from_db()
    tokens = drafted.usage.input_tokens + drafted.usage.output_tokens
    return DraftOutcome(gap=gap, status=gap.status, tokens=tokens)


# --- 3. Publish (human action) ----------------------------------------------------------------


def publish_gap(
    gap: KnowledgeGap,
    *,
    user: AbstractBaseUser,
    session_factory: SessionFactory | None = None,
    uploader: Uploader | None = None,
) -> KnowledgeGap:
    """A human publishes the reviewed draft to Meko's knowledge base."""
    gap.refresh_from_db()
    if gap.status not in (KnowledgeGap.Status.DRAFTED, KnowledgeGap.Status.NEEDS_AUTHOR):
        raise ReviewError(f"Only drafted gaps can be published (this one is {gap.get_status_display().lower()}).")
    if not gap.draft_title.strip() or not gap.draft_body.strip():
        raise ReviewError("Write a title and an article body before publishing.")
    previous = gap.status
    claimed = KnowledgeGap.objects.filter(pk=gap.pk, status=previous).update(
        status=KnowledgeGap.Status.PUBLISHING, published_by=user, published_at=timezone.now()
    )
    if not claimed:
        raise ReviewError("This gap is already being published.")
    gap.refresh_from_db()  # publish the text as it is now, not as it was when the page loaded

    source_ticket = gap.ticket.external_id if gap.ticket_id and approved_fixes_for(gap) else ""
    content = compose_article(title=gap.draft_title, body=gap.draft_body, source_ticket=source_ticket)
    calls: list[MekoCallRecord] = []

    async def publish():
        async with _factory(session_factory)(calls) as meko:
            return await publish_article(
                meko, gap_ref=f"gap-{gap.pk}", filename=f"dejafix-gap-{gap.pk}.md", content=content,
                uploader=uploader or default_uploader(),
            )

    try:
        uploaded = async_to_sync(publish)()
    except MekoError:
        # Release the claim so the reviewer can retry once Meko is back.
        KnowledgeGap.objects.filter(pk=gap.pk, status=KnowledgeGap.Status.PUBLISHING).update(
            status=previous, published_by=None, published_at=None
        )
        raise

    KnowledgeGap.objects.filter(pk=gap.pk, status=KnowledgeGap.Status.PUBLISHING).update(
        status=KnowledgeGap.Status.PUBLISHED, document_key=uploaded.key, updated_at=timezone.now()
    )
    gap.refresh_from_db()
    return gap


def dismiss_gap(gap: KnowledgeGap) -> KnowledgeGap:
    dismissed = KnowledgeGap.objects.filter(pk=gap.pk, status__in=KnowledgeGap.EDITABLE_STATES).update(
        status=KnowledgeGap.Status.DISMISSED, updated_at=timezone.now()
    )
    if not dismissed:
        raise ReviewError("Published gaps can't be dismissed.")
    gap.refresh_from_db()
    return gap


# --- 4. Verify --------------------------------------------------------------------------------


@dataclass
class VerifyReport:
    checked: list[dict[str, object]] = field(default_factory=list)
    indexing: int = 0
    stopped_early: str = ""
    report_hash: str = ""


def verify_gaps(*, limit: int = 5, session_factory: SessionFactory | None = None) -> VerifyReport:
    """Replay the searches behind published gaps and record their band after the fix."""
    if not 1 <= limit <= MAX_VERIFY_PER_RUN:
        raise ValueError(f"limit must be between 1 and {MAX_VERIFY_PER_RUN}")
    gaps = list(
        KnowledgeGap.objects.filter(status__in=[KnowledgeGap.Status.PUBLISHED, KnowledgeGap.Status.STILL_WEAK])
        .order_by("id")[:limit]
    )
    report = VerifyReport()
    if not gaps:
        return report
    calls: list[MekoCallRecord] = []
    results: dict[int, VerifyResult] = {}
    ranks: dict[int, int | None] = {}

    async def run():
        async with _factory(session_factory)(calls) as root:
            trace = AgentTrace(root, agent=LIBRARIAN_AGENT, ticket_ref=f"gaps-verify-{timezone.now():%Y%m%d-%H%M%S}")
            librarian = await trace.open()
            for gap in gaps:
                state = await document_state(librarian, gap.document_key)
                if state not in INDEXED_STATES:
                    report.indexing += state not in FAILED_STATES
                    continue
                try:
                    result = await verify_gap(librarian, log_id=gap.log_id, test_prompt_id=gap.test_prompt_id)
                except MekoToolError as exc:
                    if exc.code in RATE_LIMIT_CODES:
                        report.stopped_early = "Meko's replay limit was reached; run again in a minute."
                        break
                    raise
                results[gap.pk] = result
                report.indexing += result.indexing
                # Attribution: does the knowledge base itself now answer the search?
                ranks[gap.pk] = await article_rank(librarian, query=gap.query, title=gap.draft_title)
            rows = [_report_row(gap, results[gap.pk], ranks.get(gap.pk)) for gap in gaps if gap.pk in results]
            if rows:
                report.report_hash = await put_artifact(
                    librarian,
                    filename=f"dejafix-gap-report-{timezone.now():%Y%m%d-%H%M%S}.json",
                    content=json.dumps({"app": "dejafix", "kind": "gap_report", "gaps": rows}, indent=2, sort_keys=True).encode("utf-8"),
                    content_type=JSON_CONTENT_TYPE,
                )
                await trace.record(
                    "verify_gaps",
                    input_text=f"{len(rows)} published article(s)",
                    output={"verified": sum(row["band_after"] == "high" for row in rows), "checked": len(rows), "report": report.report_hash},
                    reasoning=(
                        "Replaying each weak search shows whether the gap closed; the top source and the "
                        "article's knowledge-base rank show whether the article itself closed it."
                    ),
                )
            report.checked = rows

    async_to_sync(run)()
    now = timezone.now()
    with transaction.atomic():
        for gap in gaps:
            result = results.get(gap.pk)
            if result is None or result.indexing:
                continue
            KnowledgeGap.objects.filter(pk=gap.pk, status=gap.status).update(
                status=KnowledgeGap.Status.VERIFIED if result.verified else KnowledgeGap.Status.STILL_WEAK,
                band_after=result.band_after,
                score_after=result.score_after,
                source_after=result.top_source,
                kb_rank_after=ranks.get(gap.pk),
                test_prompt_id=result.test_prompt_id or gap.test_prompt_id,
                verified_at=now,
                updated_at=now,
            )
    return report


def _report_row(gap: KnowledgeGap, result: VerifyResult, kb_rank: int | None) -> dict[str, object]:
    return {
        "gap": gap.pk,
        "query": gap.query,
        "band_before": gap.band_before,
        "score_before": gap.score_before,
        "band_after": result.band_after,
        "score_after": result.score_after,
        "top_source_after": result.top_source,
        "article_kb_rank": kb_rank,
        "indexing": result.indexing,
    }
