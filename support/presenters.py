"""What the ticket page shows, computed from the stored runs: the five-stage agent pipeline."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from support.models import AgentRun, Resolution, Ticket

PENDING, CURRENT, DONE, SKIPPED, STOPPED, REJECTED = "pending", "current", "done", "skipped", "stopped", "rejected"


@dataclass(frozen=True)
class Stage:
    key: str
    label: str
    agent: str
    state: str
    detail: str
    tokens: int = 0


def pipeline(ticket: Ticket, resolution: Resolution | None, runs: Sequence[AgentRun]) -> list[Stage]:
    """Triage → Retrieve → Resolve → Review → Human, each with its state and one line of detail."""
    by_step = {run.step: run for run in runs}
    triage, retrieve = by_step.get("triage"), by_step.get("retrieve")
    resolve = by_step.get("reuse_check") or by_step.get("solve")
    review = by_step.get("review")
    reused = resolution is not None and resolution.source == Resolution.Source.REUSED
    escalated = ticket.status == Ticket.Status.ESCALATED
    after_stop = STOPPED if escalated else PENDING

    stages = [
        Stage("triage", "Triage", "dejafix-triage", DONE if triage else PENDING,
              _triage_detail(triage) if triage else "Category, language, injection check", _tokens(triage)),
        Stage("retrieve", "Retrieve", "resolver · Meko search", DONE if retrieve else (after_stop if triage else PENDING),
              _retrieve_detail(retrieve) if retrieve else "Approved fixes, corrections, docs"),
        Stage("resolve", "Resolve", "resolver", DONE if resolve else (after_stop if triage else PENDING),
              _resolve_detail(by_step, resolution) if resolve else "Reuse a fix or solve from the docs", _tokens(resolve)),
    ]
    if reused:
        stages.append(Stage("review", "Review", "dejafix-reviewer", SKIPPED, "Not needed: the fix was approved by a human before"))
    else:
        stages.append(Stage("review", "Review", "dejafix-reviewer", DONE if review else (after_stop if triage else PENDING),
                            _review_detail(review) if review else "Policy and evidence check", _tokens(review)))
    stages.append(_human_stage(ticket, resolution, reused, escalated))
    return stages


def _tokens(run: AgentRun | None) -> int:
    return run.total_tokens if run else 0


def _triage_detail(run: AgentRun) -> str:
    decision = run.decision or {}
    parts = [str(decision.get("category") or "").strip(), str(decision.get("language") or "").upper()]
    if decision.get("contains_agent_instructions"):
        parts.append("injection flagged")
    return " · ".join(part for part in parts if part) or "Triaged"


def _retrieve_detail(run: AgentRun) -> str:
    decision = run.decision or {}
    fixes, learnings = len(decision.get("approved_fixes") or []), int(decision.get("learnings") or 0)
    return f"{fixes} approved fix{'es' if fixes != 1 else ''} · {learnings} learning{'s' if learnings != 1 else ''}"


def _resolve_detail(by_step: dict[str, AgentRun], resolution: Resolution | None) -> str:
    check = by_step.get("reuse_check")
    if resolution is not None and resolution.source == Resolution.Source.REUSED and check:
        score = (check.decision or {}).get("score")
        return f"Reused an approved fix (score {score:.2f})" if isinstance(score, (int, float)) else "Reused an approved fix"
    if check and "solve" in by_step:
        return "Approved fix didn't apply; solved from the docs"
    return "Solved from the documentation"


def _review_detail(run: AgentRun) -> str:
    verdict = str((run.decision or {}).get("verdict") or "").replace("_", " ")
    return verdict.capitalize() if verdict else "Reviewed"


def _human_stage(ticket: Ticket, resolution: Resolution | None, reused: bool, escalated: bool) -> Stage:
    label, agent = "Human", "you"
    if escalated:
        return Stage("human", label, agent, CURRENT, "Escalated: a person must handle it")
    if resolution is None:
        return Stage("human", label, agent, PENDING, "Approve or reject the fix")
    if reused:
        return Stage("human", label, agent, SKIPPED, "Not needed: reused a human-approved fix")
    reviewer = resolution.reviewed_by.get_username() if resolution.reviewed_by else "staff"
    if resolution.review_status == Resolution.Review.APPROVED:
        return Stage("human", label, agent, DONE, f"Approved by {reviewer}")
    if resolution.review_status == Resolution.Review.REJECTED:
        return Stage("human", label, agent, REJECTED, f"Rejected by {reviewer}, with a note")
    return Stage("human", label, agent, CURRENT, "Waiting for your review")
