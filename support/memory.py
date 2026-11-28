"""The shared-memory page: every human-approved fix in Meko's collective memory, the tickets that
reused it and what that saved, the fixes a human retired, and every human correction the resolvers
learn from.

All of it comes from the local database (each approval and reuse is recorded there with its Meko
memory ID), in a fixed number of queries. Nothing here calls Meko.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from django.db.models import F, Sum

from support.models import AgentRun, Resolution

# The page shows the most recent entries; older ones stay in the admin.
MAX_FIXES = 50
MAX_CORRECTIONS = 50


@dataclass(frozen=True)
class Reuse:
    ticket_ref: str
    subject: str
    tokens: int
    saved: int
    at: datetime


@dataclass(frozen=True)
class SharedFix:
    memory_id: str
    ticket_ref: str
    subject: str
    signature: str
    summary: str
    steps: list[str]
    approved_by: str
    approved_at: datetime | None
    tokens: int
    case_file_hash: str
    reuses: list[Reuse] = field(default_factory=list)
    retired_by: str = ""
    retired_at: datetime | None = None
    retirement_note: str = ""
    retirement_case_file_hash: str = ""

    @property
    def tokens_saved(self) -> int:
        return sum(reuse.saved for reuse in self.reuses)


@dataclass(frozen=True)
class Correction:
    ticket_ref: str
    subject: str
    signature: str
    rejected_summary: str
    note: str
    by: str
    at: datetime | None


@dataclass(frozen=True)
class MemoryOverview:
    fixes: list[SharedFix]
    corrections: list[Correction]
    retired: list[SharedFix] = field(default_factory=list)

    @property
    def total_reuses(self) -> int:
        return sum(len(fix.reuses) for fix in self.fixes)

    @property
    def tokens_saved(self) -> int:
        return sum(fix.tokens_saved for fix in self.fixes)

    @property
    def learning_count(self) -> int:
        """Rejection notes plus retirement reasons: each one is a human correction in Meko."""
        return len(self.corrections) + len(self.retired)


def _username(resolution: Resolution) -> str:
    return resolution.reviewed_by.get_username() if resolution.reviewed_by else "staff"


def _retired_by(resolution: Resolution) -> str:
    if not resolution.retired_at:
        return ""
    return resolution.retired_by.get_username() if resolution.retired_by else "staff"


def overview() -> MemoryOverview:
    """Four queries: approved fixes (shared and retired), their reuses, token sums for all those
    tickets, corrections."""
    approved = list(
        Resolution.objects.filter(review_status=Resolution.Review.APPROVED)
        .exclude(promoted_memory_id="")
        .select_related("ticket", "reviewed_by", "retired_by")
        .order_by("-reviewed_at", "-id")[:MAX_FIXES]
    )
    memory_ids = [resolution.promoted_memory_id for resolution in approved]
    reuses = list(
        Resolution.objects.filter(source=Resolution.Source.REUSED, reused_memory_id__in=memory_ids)
        .select_related("ticket")
        .order_by("created_at", "id")
    )
    ticket_ids = {r.ticket_id for r in approved} | {r.ticket_id for r in reuses}
    tokens = {
        row["ticket_id"]: row["tokens"] or 0
        for row in AgentRun.objects.filter(ticket_id__in=ticket_ids)
        .values("ticket_id")
        .annotate(tokens=Sum(F("input_tokens") + F("output_tokens")))
    }
    reuses_by_memory: dict[str, list[Resolution]] = defaultdict(list)
    for reuse in reuses:
        reuses_by_memory[reuse.reused_memory_id].append(reuse)

    fixes, retired = [], []
    for resolution in approved:
        source_tokens = tokens.get(resolution.ticket_id, 0)
        (retired if resolution.retired_at else fixes).append(
            SharedFix(
                memory_id=resolution.promoted_memory_id,
                ticket_ref=resolution.ticket.external_id,
                subject=resolution.ticket.subject,
                signature=resolution.problem_signature,
                summary=resolution.fix_summary,
                steps=list(resolution.fix_steps or []),
                approved_by=_username(resolution),
                approved_at=resolution.reviewed_at,
                tokens=source_tokens,
                case_file_hash=resolution.case_file_hash,
                retired_by=_retired_by(resolution),
                retired_at=resolution.retired_at,
                retirement_note=resolution.retirement_note,
                retirement_case_file_hash=resolution.retirement_case_file_hash,
                reuses=[
                    Reuse(
                        ticket_ref=reuse.ticket.external_id,
                        subject=reuse.ticket.subject,
                        tokens=tokens.get(reuse.ticket_id, 0),
                        saved=max(source_tokens - tokens.get(reuse.ticket_id, 0), 0),
                        at=reuse.created_at,
                    )
                    for reuse in reuses_by_memory.get(resolution.promoted_memory_id, [])
                ],
            )
        )

    corrections = [
        Correction(
            ticket_ref=resolution.ticket.external_id,
            subject=resolution.ticket.subject,
            signature=resolution.problem_signature,
            rejected_summary=resolution.fix_summary,
            note=resolution.human_note,
            by=_username(resolution),
            at=resolution.reviewed_at,
        )
        for resolution in Resolution.objects.filter(review_status=Resolution.Review.REJECTED)
        .exclude(human_note="")
        .select_related("ticket", "reviewed_by")
        .order_by("-reviewed_at", "-id")[:MAX_CORRECTIONS]
    ]
    return MemoryOverview(fixes=fixes, corrections=corrections, retired=retired)
