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
    wave: int  # 1 = the issue's first occurrence in order, 2 = a repeat
    injection: bool


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
        issue = raw["expected_issue"]
        if issue not in issues:
            raise ValueError(f"{raw['external_id']}: unknown issue {issue!r}")
        tickets.append(
            BenchTicket(
                ref=raw["external_id"],
                subject=raw["subject"],
                body=raw["body"],
                issue=issue,
                wave=2 if issue in seen else 1,
                injection=bool(raw.get("injection", False)),
            )
        )
        seen.add(issue)
    return GroundTruth(issues=issues, tickets=tuple(tickets))


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
