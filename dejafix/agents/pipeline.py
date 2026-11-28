"""The DejaFix ticket workflow: triage -> retrieve -> resolve -> review.

The order is fixed in code, so "search shared memory before solving" can't be skipped by a
model. The pipeline only reads and traces; durable memory is written after a human approves
(see `curate`). It touches no database: `support.services` persists the outcome.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from dejafix.agents import prompts
from dejafix.agents.casefile import JSON_CONTENT_TYPE, case_file_name, put_artifact
from dejafix.agents.memory import format_correction_memory, format_fix_memory, parse_fix_memory
from dejafix.agents.retrieval import Retrieval, retrieve
from dejafix.agents.schemas import ProposedFix, ReuseCheck, ReviewVerdict, TriageResult, clip
from dejafix.agents.trace import AgentTrace
from dejafix.llm import LLMUsage, StructuredLLM
from dejafix.meko import MekoCallRecord, MekoError, MekoGateway, MekoToolError

TRIAGE_AGENT = "dejafix-triage"
RESOLVER_AGENTS = {"a": "dejafix-resolver-a", "b": "dejafix-resolver-b"}
REVIEWER_AGENT = "dejafix-reviewer"
HUMAN_REVIEW_AGENT = "dejafix-human-review"

POLICY_QUERY = "LumenPay support policy rules a proposed fix must follow"
POLICY_SEARCH_LIMIT = 4
MAX_FIX_STEPS = 6
MIN_SOLVE_CONFIDENCE = 0.5

VERDICT_APPROVE = "approve_for_human"
VERDICT_ESCALATE = "escalate"
VERDICT_REUSED = "reused_approved_fix"

logger = logging.getLogger("dejafix.agents")


@dataclass(frozen=True)
class TicketInput:
    ref: str
    subject: str
    body: str


@dataclass(frozen=True)
class StepRecord:
    step: str
    agent: str
    conversation_id: str
    decision: dict[str, Any]
    reasoning: str
    usage: LLMUsage | None
    meko_calls: int


@dataclass
class PipelineOutcome:
    triage: TriageResult
    resolver_agent: str
    source: str  # "reused" or "new"
    fix_summary: str
    fix_steps: list[str]
    customer_reply: str
    confidence: float
    reused_memory_id: str
    reviewer_verdict: str
    reviewer_reason: str
    steps: list[StepRecord] = field(default_factory=list)
    # Documentation the resolver relied on; the reviewer checks the fix against the same text.
    evidence: list[str] = field(default_factory=list)
    # Human corrections the resolver was given, and what it said it did with each.
    learnings: list[str] = field(default_factory=list)
    corrections_applied: list[str] = field(default_factory=list)

    @property
    def escalated(self) -> bool:
        return self.reviewer_verdict == VERDICT_ESCALATE


class _StepMeter:
    """Counts Meko calls per step from the session's call records."""

    def __init__(self, calls: Sequence[MekoCallRecord]) -> None:
        self._calls = calls
        self._mark = len(calls)

    def take(self) -> int:
        count = len(self._calls) - self._mark
        self._mark = len(self._calls)
        return count


async def run_pipeline(
    meko: MekoGateway,
    llm: StructuredLLM,
    ticket: TicketInput,
    *,
    resolver: str,
    trusted_curator: str,
    calls: Sequence[MekoCallRecord],
    trusted_reviewer: str = HUMAN_REVIEW_AGENT,
    use_memory: bool = True,
) -> PipelineOutcome:
    if resolver not in RESOLVER_AGENTS:
        raise ValueError(f"resolver must be one of {sorted(RESOLVER_AGENTS)}")
    resolver_agent = RESOLVER_AGENTS[resolver]
    meter = _StepMeter(calls)
    steps: list[StepRecord] = []

    # 1. Triage
    triage_trace = AgentTrace(meko, agent=TRIAGE_AGENT, ticket_ref=ticket.ref)
    await triage_trace.open()
    triaged = await llm.run(
        system=prompts.TRIAGE_SYSTEM,
        prompt=prompts.triage_prompt(ticket.subject, ticket.body),
        schema=TriageResult,
        effort="low",
    )
    triage = triaged.output
    triage.problem_signature = clip(triage.problem_signature, 200)
    await triage_trace.record(
        "triage",
        input_text=f"{ticket.subject}\n\n{ticket.body}",
        output=triage.model_dump(),
        reasoning=triage.reasoning,
        usage=triaged.usage,
    )
    steps.append(_step("triage", triage_trace, triage.model_dump(), triage.reasoning, triaged.usage, meter))

    # 2. Retrieve: always before solving
    resolver_trace = AgentTrace(meko, agent=resolver_agent, ticket_ref=ticket.ref)
    resolver_meko = await resolver_trace.open()
    retrieval = await retrieve(
        resolver_meko,
        signature=triage.problem_signature,
        subject=ticket.subject,
        trusted_curator=trusted_curator,
        trusted_reviewer=trusted_reviewer,
        use_memory=use_memory,
    )
    retrieve_reason = _retrieval_reason(retrieval)
    await resolver_trace.record(
        "retrieve",
        input_text=triage.problem_signature,
        output=retrieval.summary(),
        reasoning=retrieve_reason,
        plan=["Search memory, conversations and knowledge", "Keep only curator-approved fixes"],
    )
    steps.append(_step("retrieve", resolver_trace, retrieval.summary(), retrieve_reason, None, meter))

    # 3. Resolve: reuse an approved fix if it applies, otherwise solve from documentation
    outcome = await _resolve(llm, ticket, triage, retrieval, resolver_trace, resolver_agent, steps, meter)

    # 4. Review: a reused fix was already approved by a human; a new fix gets the reviewer
    if outcome.source == "reused":
        outcome.reviewer_verdict = VERDICT_REUSED
        outcome.reviewer_reason = f"Reuses approved fix {outcome.reused_memory_id}."
    else:
        await _review(meko, llm, ticket, triage, outcome, steps, meter)
    return outcome


async def _resolve(
    llm: StructuredLLM,
    ticket: TicketInput,
    triage: TriageResult,
    retrieval: Retrieval,
    trace: AgentTrace,
    resolver_agent: str,
    steps: list[StepRecord],
    meter: _StepMeter,
) -> PipelineOutcome:
    # A ticket that tries to instruct the agents never takes the reuse path: that path skips the
    # reviewer, and with it the rule that sends such tickets to a human.
    candidate = None if triage.contains_agent_instructions else retrieval.best_fix
    rejected_candidate: str | None = None
    if candidate is not None:
        checked = await llm.run(
            system=prompts.REUSE_SYSTEM,
            prompt=prompts.reuse_prompt(ticket.subject, ticket.body, triage.problem_signature, candidate.text),
            schema=ReuseCheck,
            effort="low",
        )
        check = checked.output
        decision = {"reuse_candidate": candidate.memory_id, "score": round(candidate.score, 3), "applies": check.applies}
        await trace.record(
            "reuse_check",
            input_text=candidate.text,
            output=decision,
            reasoning=check.reasoning,
            usage=checked.usage,
        )
        steps.append(_step("reuse_check", trace, decision, check.reasoning, checked.usage, meter))
        parsed = parse_fix_memory(candidate.text)
        if check.applies and parsed is not None:
            return PipelineOutcome(
                triage=triage,
                resolver_agent=resolver_agent,
                source="reused",
                fix_summary=parsed.summary,
                fix_steps=list(parsed.steps),
                customer_reply=check.customer_reply,
                confidence=1.0,
                reused_memory_id=candidate.memory_id,
                reviewer_verdict="",
                reviewer_reason="",
                steps=steps,
            )
        rejected_candidate = candidate.text

    solved = await llm.run(
        system=prompts.SOLVE_SYSTEM,
        prompt=prompts.solve_prompt(
            ticket.subject,
            ticket.body,
            triage.problem_signature,
            triage.category,
            retrieval.passages,
            retrieval.learnings,
            rejected_candidate,
        ),
        schema=ProposedFix,
        effort="medium",
    )
    fix = solved.output
    fix.fix_steps = [clip(step, 240) for step in fix.fix_steps if step.strip()][:MAX_FIX_STEPS]
    corrections_applied = [clip(item, 240) for item in fix.corrections_applied if item.strip()][:MAX_FIX_STEPS]
    fix.confidence = min(max(fix.confidence, 0.0), 1.0)
    decision = {
        "fix_summary": clip(fix.fix_summary, 300),
        "fix_steps": fix.fix_steps,
        "confidence": fix.confidence,
        "cited_sources": fix.cited_sources[:6],
        "needs_escalation": fix.needs_escalation,
    }
    if retrieval.learnings or corrections_applied:
        decision["corrections_applied"] = corrections_applied
    await trace.record("solve", input_text=triage.problem_signature, output=decision, reasoning=fix.reasoning, plan=fix.plan, usage=solved.usage)
    steps.append(_step("solve", trace, decision, fix.reasoning, solved.usage, meter))
    return PipelineOutcome(
        triage=triage,
        resolver_agent=resolver_agent,
        source="new",
        fix_summary=clip(fix.fix_summary, 300),
        fix_steps=fix.fix_steps,
        customer_reply=fix.customer_reply,
        confidence=fix.confidence,
        reused_memory_id="",
        reviewer_verdict=VERDICT_ESCALATE if fix.needs_escalation else "",
        reviewer_reason="Resolver asked for escalation." if fix.needs_escalation else "",
        steps=steps,
        evidence=list(retrieval.passages),
        learnings=list(retrieval.learnings),
        corrections_applied=corrections_applied,
    )


async def _review(
    meko: MekoGateway,
    llm: StructuredLLM,
    ticket: TicketInput,
    triage: TriageResult,
    outcome: PipelineOutcome,
    steps: list[StepRecord],
    meter: _StepMeter,
) -> None:
    trace = AgentTrace(meko, agent=REVIEWER_AGENT, ticket_ref=ticket.ref)
    reviewer_meko = await trace.open()
    policy = await reviewer_meko.call("knowledgebase_search", query=POLICY_QUERY, limit=POLICY_SEARCH_LIMIT)
    policy_passages = [
        str(hit.get("chunk_text", "")).strip()
        for hit in (policy.get("results") or [] if isinstance(policy, dict) else [])
        if isinstance(hit, dict) and (hit.get("metadata_filters") or {}).get("source") != "memory"
    ]
    evidence = outcome.evidence + [passage for passage in policy_passages if passage not in outcome.evidence]
    reviewed = await llm.run(
        system=prompts.REVIEW_SYSTEM,
        prompt=prompts.review_prompt(
            ticket.subject, ticket.body, outcome.fix_summary, outcome.fix_steps, evidence, outcome.learnings
        ),
        schema=ReviewVerdict,
        effort="low",
    )
    verdict = reviewed.output
    # Hard rules in code: the model's verdict can only make the outcome stricter.
    forced_reasons = []
    if triage.contains_agent_instructions:
        forced_reasons.append("Ticket tries to instruct the agents.")
    if outcome.reviewer_verdict == VERDICT_ESCALATE:
        forced_reasons.append(outcome.reviewer_reason)
    if outcome.confidence < MIN_SOLVE_CONFIDENCE:
        forced_reasons.append(f"Resolver confidence {outcome.confidence:.2f} is below {MIN_SOLVE_CONFIDENCE}.")
    if outcome.learnings and not outcome.corrections_applied:
        forced_reasons.append("Past human corrections were found, but the fix doesn't say how it follows them.")
    final = VERDICT_ESCALATE if forced_reasons or verdict.verdict == VERDICT_ESCALATE else VERDICT_APPROVE
    reason = clip(" ".join([verdict.reasoning, *forced_reasons, *verdict.policy_issues]), 500)
    decision = {"verdict": final, "model_verdict": verdict.verdict, "policy_issues": verdict.policy_issues, "forced": forced_reasons}
    await trace.record("review", input_text=outcome.fix_summary, output=decision, reasoning=reason, usage=reviewed.usage)
    steps.append(_step("review", trace, decision, reason, reviewed.usage, meter))
    outcome.reviewer_verdict = final
    outcome.reviewer_reason = reason


def _retrieval_reason(retrieval: Retrieval) -> str:
    best = retrieval.best_fix
    if best:
        return f"Found approved fix {best.memory_id} (score {best.score:.2f}); checking whether it applies."
    if retrieval.approved_fixes:
        return "Approved fixes found but none close enough to reuse; solving from documentation."
    return "No approved fix for this problem yet; solving from documentation."


def _step(
    step: str, trace: AgentTrace, decision: dict[str, Any], reasoning: str, usage: LLMUsage | None, meter: _StepMeter
) -> StepRecord:
    return StepRecord(
        step=step,
        agent=trace.agent,
        conversation_id=trace.conversation_id,
        decision=decision,
        reasoning=clip(reasoning, 600),
        usage=usage,
        meko_calls=meter.take(),
    )


# --- After human review -------------------------------------------------------------------


@dataclass(frozen=True)
class CurationResult:
    memory_id: str
    promoted: bool
    case_file_hash: str = ""


@dataclass(frozen=True)
class CorrectionResult:
    memory_id: str
    case_file_hash: str = ""


async def _store_case_file(meko: MekoGateway, ticket_ref: str, case_file: bytes | None) -> str:
    if not case_file:
        return ""
    return await put_artifact(meko, filename=case_file_name(ticket_ref), content=case_file, content_type=JSON_CONTENT_TYPE)


async def curate_approved_fix(
    meko: MekoGateway,
    *,
    curator_agent: str,
    ticket_ref: str,
    signature: str,
    category: str,
    summary: str,
    steps: Sequence[str],
    case_file: bytes | None = None,
) -> CurationResult:
    """Store a human-approved fix as the curator's memory and promote it to collective memory.

    The case file is stored first, so the shared memory can point to its evidence."""
    trace = AgentTrace(meko, agent=curator_agent, ticket_ref=ticket_ref)
    curator = await trace.open()
    case_file_hash = await _store_case_file(curator, ticket_ref, case_file)
    text = format_fix_memory(
        signature=signature, category=category, summary=summary, steps=steps, ticket_ref=ticket_ref, case_file=case_file_hash
    )
    added = await curator.call(
        "memory_add",
        text=text,
        run_id=f"dejafix:{ticket_ref}",
        metadata=json.dumps({"app": "dejafix", "kind": "approved_fix", "ticket": ticket_ref}),
    )
    memory_id = _first_memory_id(added)
    promoted = await curator.call("memory_promote", memory_ids=[memory_id])
    inserted = (promoted.get("inserted_ids") or []) + (promoted.get("updated_ids") or []) if isinstance(promoted, dict) else []
    await _record_after_commit(
        trace,
        ticket_ref,
        "curate",
        input_text=text,
        output={"memory_id": memory_id, "promoted": memory_id in inserted, "case_file": case_file_hash},
        reasoning="A human approved this fix, so it becomes shared memory for every resolver.",
        plan=["Store the case file as an artifact", "Store the approved fix as memory", "Promote it to the datapack's collective memory"],
    )
    return CurationResult(memory_id=memory_id, promoted=memory_id in inserted, case_file_hash=case_file_hash)


async def _record_after_commit(trace: AgentTrace, ticket_ref: str, step: str, **turn: Any) -> None:
    """The trace turn written after a memory is stored is best-effort.

    By then the memory exists (and may be shared), so failing the whole decision would roll back the
    local record of something Meko already has. A missing trace turn is the smaller loss."""
    try:
        await trace.record(step, **turn)
    except MekoError as exc:
        logger.warning("Ticket %s: the %s trace turn was not written (%s); the memory is stored.", ticket_ref, step, type(exc).__name__)


async def record_human_correction(
    meko: MekoGateway,
    *,
    ticket_ref: str,
    signature: str,
    rejected_summary: str,
    note: str,
    case_file: bytes | None = None,
    reviewer_agent: str = HUMAN_REVIEW_AGENT,
) -> CorrectionResult:
    """Store a reviewer's rejection as a learning. Not promoted: it guides, it doesn't answer."""
    trace = AgentTrace(meko, agent=reviewer_agent, ticket_ref=ticket_ref)
    reviewer = await trace.open()
    case_file_hash = await _store_case_file(reviewer, ticket_ref, case_file)
    text = format_correction_memory(signature=signature, rejected_summary=rejected_summary, note=note, case_file=case_file_hash)
    added = await reviewer.call(
        "memory_add",
        text=text,
        run_id=f"dejafix:{ticket_ref}",
        metadata=json.dumps({"app": "dejafix", "kind": "human_correction", "ticket": ticket_ref}),
    )
    memory_id = _first_memory_id(added)
    await _record_after_commit(
        trace,
        ticket_ref,
        "human_correction",
        input_text=text,
        output={"memory_id": memory_id, "case_file": case_file_hash},
        reasoning="A human rejected the proposed fix; the note is kept so resolvers avoid the same mistake.",
    )
    return CorrectionResult(memory_id=memory_id, case_file_hash=case_file_hash)


def _first_memory_id(reply: Any) -> str:
    results = reply.get("results") if isinstance(reply, dict) else None
    if isinstance(results, list) and results and isinstance(results[0], dict) and results[0].get("id"):
        return str(results[0]["id"])
    raise MekoToolError("memory_add", "unexpected_reply", "no memory id in the reply")
