"""Benchmark logic with no I/O: ground truth, grading, the simulated reviewer and threshold calibration.

The benchmark compares the same agents with and without Meko's shared memory. Grading is
deterministic (ground-truth keywords per issue), so two runs are compared on the same scale and
anyone can re-check a verdict by reading the fix.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# Thresholds to evaluate for REUSE_MIN_RERANK_SCORE. Meko's rerank scores bunch up near 1.0
# (live: 0.998-0.9999 for related documents), hence the fine steps at the top.
THRESHOLDS = (0.50, 0.70, 0.80, 0.90, 0.95, 0.98, 0.99, 0.995, 0.999)
ORACLE_NOTE_PREFIX = "Simulated reviewer: the fix is incomplete. It must also cover:"


@dataclass(frozen=True)
class BenchTicket:
    ref: str
    subject: str
    body: str
    issue: str
    wave: int  # 1 = the issue's first occurrence in order, 2 = a repeat, 3 = a lookalike
    injection: bool
    # Lookalikes only: the issue this ticket reads like. Its real issue is `issue`.
    lookalike_of: str = ""


FIRST_OCCURRENCE, REPEAT, LOOKALIKE = 1, 2, 3


@dataclass(frozen=True)
class GroundTruth:
    issues: Mapping[str, tuple[str, ...]]
    tickets: tuple[BenchTicket, ...]


def load_ground_truth(path: Path) -> GroundTruth:
    document = json.loads(path.read_text(encoding="utf-8"))
    issues = {name: tuple(keywords) for name, keywords in document["issues"].items()}
    seen: set[str] = set()
    tickets = []
    for raw in document["tickets"]:
        ref, issue = raw["external_id"], raw["expected_issue"]
        if issue not in issues:
            raise ValueError(f"{ref}: unknown issue {issue!r}")
        lookalike_of = raw.get("lookalike_of")
        if lookalike_of is not None:
            _check_lookalike(ref, issue, lookalike_of, issues, seen)
            wave = LOOKALIKE
        else:
            wave = REPEAT if issue in seen else FIRST_OCCURRENCE
        tickets.append(
            BenchTicket(
                ref=ref,
                subject=raw["subject"],
                body=raw["body"],
                issue=issue,
                wave=wave,
                injection=bool(raw.get("injection", False)),
                lookalike_of=lookalike_of or "",
            )
        )
        seen.add(issue)
    return GroundTruth(issues=issues, tickets=tuple(tickets))


def _check_lookalike(ref: str, issue: str, lookalike_of: object, issues: Mapping[str, object], seen: set[str]) -> None:
    """A lookalike tests reuse safety only if both its real issue and the one it resembles came earlier,
    so that in the Meko arm an approved fix can exist for each when the lookalike arrives."""
    if not isinstance(lookalike_of, str) or lookalike_of not in issues:
        raise ValueError(f"{ref}: lookalike_of must name a known issue, got {lookalike_of!r}")
    if lookalike_of == issue:
        raise ValueError(f"{ref}: a lookalike must resemble another issue than its own")
    missing = [name for name in (issue, lookalike_of) if name not in seen]
    if missing:
        raise ValueError(f"{ref}: lookalike comes before any ticket of {missing}")


def correction_followups(rows: Sequence[tuple[str, str, int, str, bool]]) -> tuple[int, int]:
    """For each rejected core ticket: was the next core ticket of the same issue correct?

    `rows` are (ticket ref, issue, wave, human decision, correct). Tickets run in ref order, so
    the order comes from the refs. Lookalikes (wave 3) are left out, so runs made before and
    after they existed compare. Returns (correct, total).
    """
    core = sorted((row for row in rows if row[2] in (FIRST_OCCURRENCE, REPEAT)), key=lambda row: row[0])
    correct = total = 0
    for index, (_, issue, _, decision, _) in enumerate(core):
        if decision != "rejected":
            continue
        following = next((row for row in core[index + 1:] if row[1] == issue), None)
        if following is not None:
            total += 1
            correct += following[4]
    return correct, total


@dataclass(frozen=True)
class Grade:
    found: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def correct(self) -> bool:
        return not self.missing


def grade(texts: Iterable[str], keywords: Sequence[str]) -> Grade:
    """A fix is correct when its summary, steps and reply mention every ground-truth keyword."""
    haystack = " ".join(texts).casefold()
    found = tuple(keyword for keyword in keywords if keyword.casefold() in haystack)
    missing = tuple(keyword for keyword in keywords if keyword not in found)
    return Grade(found=found, missing=missing)


@dataclass(frozen=True)
class OracleDecision:
    approve: bool
    note: str = ""


def oracle_review(result: Grade) -> OracleDecision:
    """The simulated human reviewer: approves complete fixes and names what an incomplete one misses.

    Its notes come from the ground truth, so it helps more than a hurried human would. The
    benchmark report says so.
    """
    if result.correct:
        return OracleDecision(approve=True)
    return OracleDecision(approve=False, note=f"{ORACLE_NOTE_PREFIX} {', '.join(result.missing)}.")


@dataclass(frozen=True)
class CandidateScores:
    """For one ticket: the best approved-fix score from the same issue and from other issues."""

    same_issue: float | None
    other_issue: float | None


@dataclass(frozen=True)
class ThresholdRow:
    threshold: float
    offered: int  # tickets where some approved fix cleared the threshold
    offered_same_issue: int
    available: int  # tickets that had a same-issue approved fix at all
    precision: float | None
    recall: float | None


def calibrate(observations: Sequence[CandidateScores], thresholds: Sequence[float] = THRESHOLDS) -> list[ThresholdRow]:
    """What each threshold would have offered to the reuse check (which picks the top score)."""
    available = sum(1 for seen in observations if seen.same_issue is not None)
    rows = []
    for threshold in thresholds:
        offered = offered_same = 0
        for seen in observations:
            same = seen.same_issue if seen.same_issue is not None and seen.same_issue >= threshold else None
            other = seen.other_issue if seen.other_issue is not None and seen.other_issue >= threshold else None
            if same is None and other is None:
                continue
            offered += 1
            offered_same += same is not None and (other is None or same >= other)
        rows.append(
            ThresholdRow(
                threshold=threshold,
                offered=offered,
                offered_same_issue=offered_same,
                available=available,
                precision=offered_same / offered if offered else None,
                recall=offered_same / available if available else None,
            )
        )
    return rows


@dataclass(frozen=True)
class ThresholdRange:
    """Thresholds that tie for the best result: never another issue's fix, best recall."""

    low: float
    high: float
    recall: float
    precision_tested: bool  # False if no other issue's fix was ever offered: precision is vacuous

    def contains(self, threshold: float) -> bool:
        return self.low <= threshold <= self.high


def best_threshold_range(rows: Sequence[ThresholdRow], observations: Sequence[CandidateScores]) -> ThresholdRange | None:
    """The range of thresholds the data can't tell apart, rather than one falsely precise value."""
    clean = [row for row in rows if row.offered and row.precision == 1.0]
    if not clean:
        return None
    best_recall = max(row.recall or 0.0 for row in clean)
    tied = [row.threshold for row in clean if (row.recall or 0.0) == best_recall]
    return ThresholdRange(
        low=min(tied),
        high=max(tied),
        recall=best_recall,
        precision_tested=any(seen.other_issue is not None for seen in observations),
    )


@dataclass(frozen=True)
class MarginRow:
    """One approved fix searched by its own problem line: the best same-issue and other-issue fix scores."""

    issue: str
    same_issue: float | None
    other_issue: float | None  # None: no other issue's fix came back at all
    other_issue_name: str
    ticket: str = ""
    lookalike_of: str = ""


@dataclass(frozen=True)
class MarginSummary:
    max_other: float | None
    min_same: float | None
    margin: float | None  # threshold minus the highest other-issue score
    over_threshold: int  # rows where another issue's fix reached the threshold


def reuse_margin(rows: Sequence[MarginRow], threshold: float) -> MarginSummary:
    """How far below the reuse threshold the closest wrong fix stays."""
    others = [row.other_issue for row in rows if row.other_issue is not None]
    sames = [row.same_issue for row in rows if row.same_issue is not None]
    max_other = max(others, default=None)
    return MarginSummary(
        max_other=max_other,
        min_same=min(sames, default=None),
        margin=round(threshold - max_other, 3) if max_other is not None else None,
        over_threshold=sum(1 for score in others if score >= threshold),
    )


# --- Drift: does an approved fix go stale after the product changes? --------------------------


@dataclass(frozen=True)
class DriftCase:
    """One issue whose documented answer changes after its first fix is approved (data/drift.json)."""

    issue: str
    first: BenchTicket
    repeats: tuple[BenchTicket, ...]
    update: str  # the product update, a repository-relative path
    old_keywords: tuple[str, ...]  # grade the first ticket (before the change), from data/tickets.json
    new_keywords: tuple[str, ...]  # grade the repeats (after the change)
    retire_reason: str


def load_drift(document: Mapping[str, object], truth: GroundTruth) -> tuple[DriftCase, ...]:
    tickets = {ticket.ref: ticket for ticket in truth.tickets}
    order = {ticket.ref: index for index, ticket in enumerate(truth.tickets)}
    cases = []
    for raw in document["cases"]:
        issue = raw["issue"]
        if issue not in truth.issues:
            raise ValueError(f"drift: unknown issue {issue!r}")
        refs = [raw["first"], *raw["repeats"]]
        unknown = [ref for ref in refs if ref not in tickets]
        if unknown:
            raise ValueError(f"drift {issue}: unknown tickets {unknown}")
        if any(tickets[ref].issue != issue for ref in refs):
            raise ValueError(f"drift {issue}: every ticket must be of this issue")
        first = tickets[raw["first"]]
        repeats = tuple(tickets[ref] for ref in raw["repeats"])
        if first.wave != FIRST_OCCURRENCE or not repeats or any(order[t.ref] < order[first.ref] for t in repeats):
            raise ValueError(f"drift {issue}: the first ticket must be the issue's first occurrence, before its repeats")
        new_keywords = tuple(keyword for keyword in raw["new_keywords"] if keyword.strip())
        old_keywords = tuple(truth.issues[issue])
        if not new_keywords or set(new_keywords) <= set(old_keywords):
            raise ValueError(f"drift {issue}: new_keywords must name what changed")
        reason = str(raw.get("retire_reason", "")).strip()
        if not reason:
            raise ValueError(f"drift {issue}: a retire_reason is required")
        cases.append(DriftCase(issue, first, repeats, raw["update"], old_keywords, new_keywords, reason))
    return tuple(cases)


@dataclass(frozen=True)
class DriftRow:
    run: str
    arm: str
    issue: str
    ticket: str
    phase: str  # "first" (before the change) or "repeat" (after it)
    source: str  # "new" or "reused"
    correct: bool
    stale_reuse: bool  # reused the fix approved before the change
    retired_skipped: bool
    escalated: bool
    human: bool  # a person had to look at it (a new fix, or an escalation)
    tokens: int
    llm_ms: int
    decision: str = ""  # the simulated reviewer: "approved", "rejected" or ""
    missing: tuple[str, ...] = ()
    fix_summary: str = ""
    error: str = ""


@dataclass(frozen=True)
class DriftSummary:
    repeats: int
    correct: int
    stale_reuses: int
    retired_skipped: int
    humans: int
    avg_tokens: int
    errors: int


def summarize_drift(rows: Sequence[DriftRow]) -> DriftSummary:
    """After the change: only the repeats count. Failed tickets count as wrong and leave the token average."""
    repeats = [row for row in rows if row.phase == "repeat"]
    ran = [row for row in repeats if not row.error]
    return DriftSummary(
        repeats=len(repeats),
        correct=sum(row.correct for row in repeats),
        stale_reuses=sum(row.stale_reuse for row in repeats),
        retired_skipped=sum(row.retired_skipped for row in repeats),
        humans=sum(row.human for row in ran),
        avg_tokens=round(sum(row.tokens for row in ran) / len(ran)) if ran else 0,
        errors=len(repeats) - len(ran),
    )
