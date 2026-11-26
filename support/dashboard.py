"""Numbers for the dashboard, computed in the database with a fixed number of queries.

The one external call, Meko's context quality (`selflearning_status`), has a short timeout and
a shared cache entry (it is datapack-wide, not per user). A failure shows "unavailable".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.db.models import Count, F, Q, Sum

from dejafix.agents.trace import AgentTrace
from dejafix.meko import MekoConfig, MekoError, MekoSession
from support.models import BenchmarkRun, KnowledgeGap, Resolution, Ticket

logger = logging.getLogger("dejafix.support")

QUALITY_CACHE_KEY = "dejafix:meko-context-quality"
QUALITY_CACHE_SECONDS = 300
QUALITY_FAILURE_CACHE_SECONDS = 60  # retry a failed lookup soon, but not on every page view
QUALITY_TIMEOUT_SECONDS = 5.0
DASHBOARD_AGENT = "dejafix-dashboard"


@dataclass(frozen=True)
class TicketStats:
    by_status: dict[str, int]
    total: int
    reused: int
    solved_new: int
    avg_tokens_new: float
    avg_tokens_reused: float

    @property
    def resolved(self) -> int:
        return self.reused + self.solved_new

    @property
    def reuse_rate(self) -> float:
        return self.reused / self.resolved if self.resolved else 0.0

    @property
    def tokens_saved(self) -> int:
        """What the reused tickets would have cost if solved from scratch, minus what they cost."""
        if not self.reused or not self.avg_tokens_new:
            return 0
        return max(round(self.reused * (self.avg_tokens_new - self.avg_tokens_reused)), 0)

    @property
    def human_touches(self) -> int:
        return self.solved_new


def ticket_stats() -> TicketStats:
    by_status = {row["status"]: row["n"] for row in Ticket.objects.values("status").annotate(n=Count("id"))}
    per_source = {
        row["source"]: row
        for row in Resolution.objects.values("source").annotate(
            tickets=Count("ticket", distinct=True),
            tokens=Sum(F("ticket__runs__input_tokens") + F("ticket__runs__output_tokens")),
        )
    }

    def count(source: str) -> int:
        return per_source.get(source, {}).get("tickets", 0)

    def average(source: str) -> float:
        row = per_source.get(source)
        return (row["tokens"] or 0) / row["tickets"] if row and row["tickets"] else 0.0

    return TicketStats(
        by_status=by_status,
        total=sum(by_status.values()),
        reused=count(Resolution.Source.REUSED),
        solved_new=count(Resolution.Source.NEW),
        avg_tokens_new=average(Resolution.Source.NEW),
        avg_tokens_reused=average(Resolution.Source.REUSED),
    )


@dataclass(frozen=True)
class GapStats:
    by_status: dict[str, int]
    awaiting_review: int
    closed_by_article: int


def gap_stats() -> GapStats:
    """One query: on a remote database each query is a network round trip."""
    rows = list(
        KnowledgeGap.objects.values("status").annotate(n=Count("id"), ranked_first=Count("id", filter=Q(kb_rank_after=1)))
    )
    by_status = {row["status"]: row["n"] for row in rows}
    ranked_first = {row["status"]: row["ranked_first"] for row in rows}
    return GapStats(
        by_status=by_status,
        awaiting_review=by_status.get(KnowledgeGap.Status.DRAFTED, 0) + by_status.get(KnowledgeGap.Status.NEEDS_AUTHOR, 0),
        closed_by_article=ranked_first.get(KnowledgeGap.Status.VERIFIED, 0),
    )


def latest_benchmark_pair() -> tuple[BenchmarkRun | None, BenchmarkRun | None]:
    """The most recent finished run of each arm."""
    latest: dict[str, BenchmarkRun] = {}
    for run in BenchmarkRun.objects.filter(status=BenchmarkRun.Status.DONE).order_by("-id"):
        latest.setdefault(run.arm, run)
    return latest.get(BenchmarkRun.Arm.DOCS_ONLY), latest.get(BenchmarkRun.Arm.MEKO)


# --- Meko context quality -----------------------------------------------------------------------


@dataclass(frozen=True)
class ContextQuality:
    available: bool
    pct: int | None = None
    searches: int | None = None
    open_fixes: list[str] = field(default_factory=list)


SessionFactory = Callable[[MekoConfig], MekoSession]


def default_quality_session(config: MekoConfig) -> MekoSession:
    return MekoSession(config)


def context_quality(session_factory: SessionFactory | None = None) -> ContextQuality:
    cached = cache.get(QUALITY_CACHE_KEY)
    if cached is not None:
        return ContextQuality(**cached)
    try:
        result = _fetch_quality(session_factory or default_quality_session)
    except MekoError as exc:
        logger.warning("Meko context quality unavailable: %s", type(exc).__name__)
        cache.set(QUALITY_CACHE_KEY, {"available": False}, QUALITY_FAILURE_CACHE_SECONDS)
        return ContextQuality(available=False)
    cache.set(QUALITY_CACHE_KEY, result.__dict__, QUALITY_CACHE_SECONDS)
    return result


def _fetch_quality(session_factory: SessionFactory) -> ContextQuality:
    config = replace(MekoConfig.from_settings(), timeout_seconds=QUALITY_TIMEOUT_SECONDS)

    async def fetch() -> Any:
        async with session_factory(config) as root:
            meko = await AgentTrace(root, agent=DASHBOARD_AGENT, ticket_ref="dashboard").open()
            return await meko.call("selflearning_status")

    status = async_to_sync(fetch)()
    quality = status.get("quality") if isinstance(status, dict) else None
    if not isinstance(quality, dict):
        return ContextQuality(available=False)
    pct, searches = quality.get("pct"), quality.get("searches")
    return ContextQuality(
        available=True,
        pct=pct if isinstance(pct, int) else None,
        searches=searches if isinstance(searches, int) else None,
        open_fixes=[str(item) for item in status.get("open_fixes") or []][:5],
    )


# --- Charts -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BarRow:
    """One labelled pair of horizontal bars; percentages are relative to the chart's maximum."""

    label: str
    left: float
    right: float
    left_pct: int
    right_pct: int
    unit: str = ""


def bar_rows(rows: list[tuple[str, float, float, str]]) -> list[BarRow]:
    """Each row gets its own scale (metrics differ in units), so bars compare within a row."""
    result = []
    for label, left, right, unit in rows:
        top = max(left, right) or 1
        result.append(BarRow(label, left, right, round(100 * left / top), round(100 * right / top), unit))
    return result
