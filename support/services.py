"""Ticket workflow services: run the agents, persist their outcome, apply human review.

Meko and Claude are called outside database transactions. Local writes happen afterwards in
one atomic block, so a failure halfway leaves no half-written ticket.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.utils import timezone

from dejafix.agents.pipeline import (
    PipelineOutcome,
    TicketInput,
    curate_approved_fix,
    record_human_correction,
    run_pipeline,
)
from dejafix.llm import LLMError, StructuredLLM
from dejafix.meko import MekoCallRecord, MekoConfig, MekoError, MekoSession
from support.models import MAX_NOTE_LENGTH, AgentRun, Resolution, Ticket

logger = logging.getLogger("dejafix.support")

MAX_FAILURE_REASON = 300

SessionFactory = Callable[[list[MekoCallRecord]], MekoSession]


def default_session_factory(calls: list[MekoCallRecord]) -> MekoSession:
    return MekoSession(MekoConfig.from_settings(), observer=calls.append)


def _factory(session_factory: SessionFactory | None) -> SessionFactory:
    # Looked up at call time so tests (and the admin) can swap the module-level default.
    return session_factory or default_session_factory


class ReviewError(Exception):
    """A review action is not allowed in the resolution's current state (maps to HTTP 409)."""


@dataclass(frozen=True)
class ResolveResult:
    ticket: Ticket
    ran: bool
    error: str = ""


def resolve_ticket(
    ticket: Ticket,
    *,
    llm: StructuredLLM,
    resolver: str = "a",
    session_factory: SessionFactory | None = None,
) -> ResolveResult:
    """Run the agents on one ticket. Re-running a resolved ticket is a no-op."""
    if not ticket.is_resolvable:
        return ResolveResult(ticket=ticket, ran=False)

    calls: list[MekoCallRecord] = []

    async def run() -> PipelineOutcome:
        async with _factory(session_factory)(calls) as meko:
            return await run_pipeline(
                meko,
                llm,
                TicketInput(ticket.external_id, ticket.subject, ticket.body),
                resolver=resolver,
                trusted_curator=settings.DEJAFIX_CURATOR_AGENT,
                calls=calls,
            )

    try:
        outcome = async_to_sync(run)()
    except (MekoError, LLMError) as exc:
        reason = f"{type(exc).__name__}: {exc}"[:MAX_FAILURE_REASON]
        logger.warning("Ticket %s failed: %s", ticket.external_id, reason)
        Ticket.objects.filter(pk=ticket.pk).update(status=Ticket.Status.FAILED, failure_reason=reason)
        ticket.refresh_from_db()
        return ResolveResult(ticket=ticket, ran=True, error=reason)

    _persist_outcome(ticket, outcome)
    ticket.refresh_from_db()
    return ResolveResult(ticket=ticket, ran=True)


@transaction.atomic
def _persist_outcome(ticket: Ticket, outcome: PipelineOutcome) -> None:
    locked = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if not locked.is_resolvable:  # another worker finished it first
        return
    for step in outcome.steps:
        AgentRun.objects.update_or_create(
            ticket=locked,
            step=step.step,
            defaults={
                "agent": step.agent,
                "meko_conversation_id": step.conversation_id,
                "decision": step.decision,
                "reasoning": step.reasoning,
                "model": step.usage.model if step.usage else "",
                "input_tokens": step.usage.input_tokens if step.usage else 0,
                "output_tokens": step.usage.output_tokens if step.usage else 0,
                "latency_ms": step.usage.latency_ms if step.usage else 0,
                "meko_calls": step.meko_calls,
            },
        )
    # A retry after FAILED may run fewer steps (e.g. reuse instead of solve): drop stale ones.
    AgentRun.objects.filter(ticket=locked).exclude(step__in=[step.step for step in outcome.steps]).delete()

    reused = outcome.source == Resolution.Source.REUSED
    now = timezone.now()
    Resolution.objects.update_or_create(
        ticket=locked,
        defaults={
            "resolver_agent": outcome.resolver_agent,
            "source": outcome.source,
            "category": outcome.triage.category,
            "problem_signature": outcome.triage.problem_signature,
            "fix_summary": outcome.fix_summary,
            "fix_steps": outcome.fix_steps,
            "customer_reply": outcome.customer_reply,
            "confidence": outcome.confidence,
            "reused_memory_id": outcome.reused_memory_id,
            "reviewer_verdict": outcome.reviewer_verdict,
            "reviewer_reason": outcome.reviewer_reason,
            # A reused fix was approved by a human when it was first curated.
            "review_status": Resolution.Review.APPROVED if reused else Resolution.Review.PENDING,
            "reviewed_at": now if reused else None,
            "reviewed_by": None,
            "human_note": "",
            "promoted_memory_id": "",
        },
    )
    if reused:
        locked.status = Ticket.Status.CLOSED
    elif outcome.escalated:
        locked.status = Ticket.Status.ESCALATED
    else:
        locked.status = Ticket.Status.AWAITING_REVIEW
    locked.failure_reason = ""
    locked.save(update_fields=["status", "failure_reason", "updated_at"])


def approve_resolution(
    resolution: Resolution,
    *,
    user: AbstractBaseUser,
    note: str = "",
    session_factory: SessionFactory | None = None,
) -> Resolution:
    """Human approves a new fix: the curator stores it and promotes it to collective memory."""
    if resolution.source != Resolution.Source.NEW:
        raise ReviewError("Only newly solved fixes need approval.")
    note = note.strip()[:MAX_NOTE_LENGTH]
    # Claim the decision with a conditional UPDATE so two reviewers can't both curate it.
    claimed = Resolution.objects.filter(pk=resolution.pk, review_status=Resolution.Review.PENDING).update(
        review_status=Resolution.Review.APPROVED, reviewed_by=user, reviewed_at=timezone.now(), human_note=note
    )
    if not claimed:
        raise ReviewError("This fix was already reviewed.")

    calls: list[MekoCallRecord] = []

    async def curate():
        async with _factory(session_factory)(calls) as meko:
            return await curate_approved_fix(
                meko,
                curator_agent=settings.DEJAFIX_CURATOR_AGENT,
                ticket_ref=resolution.ticket.external_id,
                signature=resolution.problem_signature,
                category=resolution.category,
                summary=resolution.fix_summary,
                steps=resolution.fix_steps,
            )

    try:
        result = async_to_sync(curate)()
    except MekoError:
        # Release the claim so the reviewer can retry once Meko is back.
        Resolution.objects.filter(pk=resolution.pk).update(
            review_status=Resolution.Review.PENDING, reviewed_by=None, reviewed_at=None
        )
        raise

    with transaction.atomic():
        Resolution.objects.filter(pk=resolution.pk).update(promoted_memory_id=result.memory_id)
        Ticket.objects.filter(pk=resolution.ticket_id).update(status=Ticket.Status.CLOSED, updated_at=timezone.now())
    resolution.refresh_from_db()
    return resolution


def reject_resolution(
    resolution: Resolution,
    *,
    user: AbstractBaseUser,
    note: str,
    session_factory: SessionFactory | None = None,
) -> Resolution:
    """Human rejects a fix with a reason; the reason becomes a learning for future resolvers."""
    note = note.strip()[:MAX_NOTE_LENGTH]
    if not note:
        raise ReviewError("A rejection needs a note explaining what was wrong.")
    claimed = Resolution.objects.filter(pk=resolution.pk, review_status=Resolution.Review.PENDING).update(
        review_status=Resolution.Review.REJECTED, reviewed_by=user, reviewed_at=timezone.now(), human_note=note
    )
    if not claimed:
        raise ReviewError("This fix was already reviewed.")

    calls: list[MekoCallRecord] = []

    async def correct():
        async with _factory(session_factory)(calls) as meko:
            return await record_human_correction(
                meko,
                ticket_ref=resolution.ticket.external_id,
                signature=resolution.problem_signature,
                rejected_summary=resolution.fix_summary,
                note=note,
            )

    try:
        async_to_sync(correct)()
    except MekoError:
        Resolution.objects.filter(pk=resolution.pk).update(
            review_status=Resolution.Review.PENDING, reviewed_by=None, reviewed_at=None, human_note=""
        )
        raise

    Ticket.objects.filter(pk=resolution.ticket_id).update(status=Ticket.Status.ESCALATED, updated_at=timezone.now())
    resolution.refresh_from_db()
    return resolution
