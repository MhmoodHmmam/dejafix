"""Run the benchmark: the same agents over the 36 tickets, with or without Meko's shared memory.

Each run has its own datapack (isolated from the demo's memory and articles) and its own
curator and reviewer identities. Tickets run in order, so in the Meko arm a fix approved by the
simulated reviewer is there for the later tickets, exactly as in production. The demo tables
(Ticket, Resolution) are never touched.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from dejafix.agents.casefile import JSON_CONTENT_TYPE, put_artifact
from dejafix.agents.pipeline import (
    PipelineOutcome,
    TicketInput,
    curate_approved_fix,
    record_human_correction,
    run_pipeline,
)
from dejafix.agents.trace import AgentTrace
from dejafix.agents.retrieval import REUSE_MIN_RERANK_SCORE
from dejafix.bench import (
    FIRST_OCCURRENCE,
    LOOKALIKE,
    REPEAT,
    BenchTicket,
    CandidateScores,
    GroundTruth,
    best_threshold_range,
    calibrate,
    grade,
    load_ground_truth,
    oracle_review,
)
from dejafix.llm import LLMError, StructuredLLM
from dejafix.meko import MekoCallRecord, MekoConfig, MekoError, MekoGateway, MekoSession, MekoToolError
from support.models import BenchmarkResult, BenchmarkRun

logger = logging.getLogger("dejafix.support")

BENCHMARK_AGENT = "dejafix-benchmark"
GROUND_TRUTH_PATH = Path("data") / "tickets.json"
MAX_ERROR_LENGTH = 300
# List prices for claude-opus-5-5 (per million tokens), used only to estimate a run's cost.
INPUT_USD_PER_MTOK = 4.0
OUTPUT_USD_PER_MTOK = 20.0

SessionFactory = Callable[[MekoConfig, list[MekoCallRecord]], MekoSession]


def default_session_factory(config: MekoConfig, calls: list[MekoCallRecord]) -> MekoSession:
    return MekoSession(config, observer=calls.append)


def ground_truth() -> GroundTruth:
    return load_ground_truth(Path(settings.BASE_DIR) / GROUND_TRUTH_PATH)


def run_config(run: BenchmarkRun) -> MekoConfig:
    return replace(MekoConfig.from_settings(), datapack_id=run.datapack_id)


# --- Setup ------------------------------------------------------------------------------------


def create_datapack(run: BenchmarkRun, *, session_factory: SessionFactory | None = None) -> BenchmarkRun:
    """Create the run's own datapack (from a conversation in the main datapack)."""
    if run.datapack_id:
        return run
    factory = session_factory or default_session_factory
    datapack_name = f"dejafix-bench-{run.name.lower()}-{secrets.token_hex(3)}"
    calls: list[MekoCallRecord] = []

    async def create() -> str:
        async with factory(MekoConfig.from_settings(), calls) as root:
            trace = AgentTrace(root, agent=BENCHMARK_AGENT, ticket_ref=f"setup-{run.name}")
            meko = await trace.open()
            reply = await meko.call("datapack_create", name=datapack_name)
            datapack_id = _datapack_id(reply)
            await trace.record(
                "create_datapack",
                input_text=datapack_name,
                output={"datapack_id": datapack_id, "arm": run.arm},
                reasoning="Each benchmark arm runs in its own datapack so no earlier memory or document leaks in.",
            )
            return datapack_id

    datapack_id = async_to_sync(create)()
    BenchmarkRun.objects.filter(pk=run.pk, datapack_id="").update(datapack_id=datapack_id, datapack_name=datapack_name)
    run.refresh_from_db()
    return run


def _datapack_id(reply: Any) -> str:
    candidates = []
    if isinstance(reply, Mapping):
        nested = reply.get("datapack") if isinstance(reply.get("datapack"), Mapping) else {}
        candidates = [reply.get("datapack_id"), reply.get("id"), nested.get("id"), nested.get("datapack_id")]
    for candidate in candidates:
        try:
            return str(uuid.UUID(str(candidate)))
        except ValueError:
            continue
    raise MekoToolError("datapack_create", "unexpected_reply", "no datapack id in the reply")


def check_documents(run: BenchmarkRun, *, session_factory: SessionFactory | None = None) -> int:
    """Count documentation chunks the run's datapack returns, so a run never starts without docs."""
    factory = session_factory or default_session_factory
    calls: list[MekoCallRecord] = []

    async def search() -> int:
        async with factory(run_config(run), calls) as root:
            meko = await AgentTrace(root, agent=BENCHMARK_AGENT, ticket_ref=f"setup-{run.name}").open()
            found = await meko.call("knowledgebase_search", query="payout schedule business days", limit=5)
            hits = found.get("results") if isinstance(found, Mapping) else None
            return sum(
                1 for hit in hits or []
                if isinstance(hit, Mapping) and (hit.get("metadata_filters") or {}).get("source") != "memory"
            )

    return async_to_sync(search)()


def mark_ready(run: BenchmarkRun) -> None:
    BenchmarkRun.objects.filter(pk=run.pk, status=BenchmarkRun.Status.SETUP).exclude(datapack_id="").update(
        status=BenchmarkRun.Status.READY
    )
    run.refresh_from_db()


# --- Running ----------------------------------------------------------------------------------


@dataclass
class RunProgress:
    ran: int = 0
    failed: int = 0
    tokens: int = 0
    stopped_by_budget: bool = False
    finished: bool = False


def spent_tokens(run: BenchmarkRun) -> int:
    totals = run.results.aggregate(inp=Sum("input_tokens"), out=Sum("output_tokens"))
    return (totals["inp"] or 0) + (totals["out"] or 0)


def run_benchmark(
    run: BenchmarkRun,
    *,
    llm: StructuredLLM,
    limit: int,
    session_factory: SessionFactory | None = None,
    truth: GroundTruth | None = None,
) -> RunProgress:
    """Run up to `limit` remaining tickets in order. Failed tickets are retried on the next call."""
    if run.status not in (BenchmarkRun.Status.READY, BenchmarkRun.Status.RUNNING):
        raise ValueError(f"run {run.name} is {run.status}; set it up first")
    truth = truth or ground_truth()
    BenchmarkRun.objects.filter(pk=run.pk).update(status=BenchmarkRun.Status.RUNNING)
    run.results.exclude(error="").delete()
    done = set(run.results.values_list("ticket_ref", flat=True))
    progress = RunProgress()

    for index, ticket in enumerate(truth.tickets):
        if ticket.ref in done:
            continue
        if progress.ran >= limit:
            break
        if spent_tokens(run) >= run.token_budget:
            progress.stopped_by_budget = True
            break
        result = _run_ticket(run, ticket, resolver="ab"[index % 2], llm=llm, truth=truth, session_factory=session_factory)
        progress.ran += 1
        progress.failed += bool(result.error)
        progress.tokens += result.total_tokens

    if run.results.filter(error="").count() >= len(truth.tickets):
        BenchmarkRun.objects.filter(pk=run.pk).update(status=BenchmarkRun.Status.DONE, finished_at=timezone.now())
        progress.finished = True
    run.refresh_from_db()
    return progress


def _approved_issues(run: BenchmarkRun) -> dict[str, str]:
    """memory id -> issue, for the fixes this run's simulated reviewer approved."""
    return dict(run.results.exclude(promoted_memory_id="").values_list("promoted_memory_id", "issue"))


def _run_ticket(
    run: BenchmarkRun,
    ticket: BenchTicket,
    *,
    resolver: str,
    llm: StructuredLLM,
    truth: GroundTruth,
    session_factory: SessionFactory | None,
) -> BenchmarkResult:
    factory = session_factory or default_session_factory
    calls: list[MekoCallRecord] = []
    keywords = truth.issues[ticket.issue]
    approved = _approved_issues(run)
    bench_ref = f"{run.name}-{ticket.ref}"

    async def solve() -> tuple[PipelineOutcome, str, str]:
        async with factory(run_config(run), calls) as meko:
            outcome = await run_pipeline(
                meko, llm, TicketInput(bench_ref, ticket.subject, ticket.body),
                resolver=resolver, trusted_curator=run.curator_agent, trusted_reviewer=run.reviewer_agent,
                calls=calls, use_memory=run.uses_memory,
            )
            decision, memory_id = await _simulated_review(meko, run, outcome, bench_ref, keywords)
            return outcome, decision, memory_id

    try:
        outcome, decision, memory_id = async_to_sync(solve)()
    except (MekoError, LLMError) as exc:
        error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
        logger.warning("Benchmark %s ticket %s failed: %s", run.name, ticket.ref, error)
        return BenchmarkResult.objects.create(
            run=run, ticket_ref=ticket.ref, issue=ticket.issue, wave=ticket.wave, injection=ticket.injection,
            keywords_total=len(keywords), meko_calls=min(len(calls), 32767), error=error,
        )

    graded = grade([outcome.fix_summary, *outcome.fix_steps, outcome.customer_reply], keywords)
    scores = _candidate_scores(outcome, approved, ticket.issue)
    reused_issue = approved.get(outcome.reused_memory_id, "unknown") if outcome.source == "reused" else ""
    usages = [step.usage for step in outcome.steps if step.usage]
    return BenchmarkResult.objects.create(
        run=run,
        ticket_ref=ticket.ref,
        issue=ticket.issue,
        wave=ticket.wave,
        injection=ticket.injection,
        resolver_agent=outcome.resolver_agent,
        source=outcome.source,
        verdict=outcome.reviewer_verdict,
        human_decision=decision,
        correct=graded.correct and not outcome.escalated,
        keywords_found=len(graded.found),
        keywords_total=len(keywords),
        missing_keywords=list(graded.missing),
        escalated=outcome.escalated,
        input_tokens=sum(usage.input_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
        llm_ms=sum(usage.latency_ms for usage in usages),
        meko_calls=min(len(calls), 32767),
        best_same_issue_score=scores.same_issue,
        best_other_issue_score=scores.other_issue,
        reused_issue=reused_issue,
        false_reuse=bool(reused_issue) and reused_issue != ticket.issue,
        promoted_memory_id=memory_id,
        fix_summary=outcome.fix_summary[:300],
    )


async def _simulated_review(
    meko: MekoGateway, run: BenchmarkRun, outcome: PipelineOutcome, bench_ref: str, keywords: Sequence[str]
) -> tuple[str, str]:
    """Meko arm only: the simulated reviewer decides each new, non-escalated fix right away."""
    if not run.uses_memory or outcome.source != "new" or outcome.escalated:
        return BenchmarkResult.HumanDecision.NONE, ""
    decision = oracle_review(grade([outcome.fix_summary, *outcome.fix_steps, outcome.customer_reply], keywords))
    if decision.approve:
        curated = await curate_approved_fix(
            meko, curator_agent=run.curator_agent, ticket_ref=bench_ref, signature=outcome.triage.problem_signature,
            category=outcome.triage.category, summary=outcome.fix_summary, steps=outcome.fix_steps,
        )
        return BenchmarkResult.HumanDecision.APPROVED, curated.memory_id
    await record_human_correction(
        meko, ticket_ref=bench_ref, signature=outcome.triage.problem_signature, rejected_summary=outcome.fix_summary,
        note=decision.note, reviewer_agent=run.reviewer_agent,
    )
    return BenchmarkResult.HumanDecision.REJECTED, ""


def _candidate_scores(outcome: PipelineOutcome, approved: Mapping[str, str], issue: str) -> CandidateScores:
    """Best approved-fix scores the resolver saw, split by whether the fix was for the same issue."""
    retrieve = next((step for step in outcome.steps if step.step == "retrieve"), None)
    fixes = retrieve.decision.get("approved_fixes", []) if retrieve else []
    same = [fix["score"] for fix in fixes if approved.get(fix.get("memory_id")) == issue]
    other = [fix["score"] for fix in fixes if fix.get("memory_id") in approved and approved[fix["memory_id"]] != issue]
    return CandidateScores(same_issue=max(same, default=None), other_issue=max(other, default=None))


# --- Report -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ArmSummary:
    run: str
    arm: str
    tickets: int
    total: int
    errors: int
    core: int  # first occurrences and repeats; lookalikes are counted apart
    correct: int
    correct_wave1: int
    wave1: int
    correct_repeats: int
    repeats: int
    tokens: int
    tokens_wave1_avg: float
    tokens_repeats_avg: float
    llm_seconds_avg: float
    meko_calls_avg: float
    reused: int
    escalated: int
    false_reuse: int
    human_touches: int
    injection_safe: int
    injections: int
    cost_usd: float
    lookalikes: int
    correct_lookalikes: int
    reused_lookalikes: int
    false_reuse_lookalikes: int


def summarize(run: BenchmarkRun, total: int, all_results: Sequence[BenchmarkResult] | None = None) -> ArmSummary:
    """`all_results`: the run's results, errored ones included, if the caller already fetched them.

    Quality metrics cover the core tickets (first occurrences and repeats) only, so a run made
    before the lookalikes existed compares with one made after. Lookalikes get their own fields.
    """
    if all_results is None:
        all_results = list(run.results.all())
    results = [result for result in all_results if not result.error]
    if run.status == BenchmarkRun.Status.DONE:
        total = len(results)  # a finished run is complete for the tickets that existed when it ran
    wave1 = [result for result in results if result.wave == FIRST_OCCURRENCE]
    repeats = [result for result in results if result.wave == REPEAT]
    lookalikes = [result for result in results if result.wave == LOOKALIKE]
    core = wave1 + repeats
    injections = [result for result in core if result.injection]
    input_tokens = sum(result.input_tokens for result in results)
    output_tokens = sum(result.output_tokens for result in results)

    def avg(values: Sequence[float]) -> float:
        return round(sum(values) / len(values), 1) if values else 0.0

    return ArmSummary(
        run=run.name,
        arm=run.arm,
        tickets=len(results),
        total=total,
        errors=len(all_results) - len(results),
        core=len(core),
        correct=sum(result.correct for result in core),
        correct_wave1=sum(result.correct for result in wave1),
        wave1=len(wave1),
        correct_repeats=sum(result.correct for result in repeats),
        repeats=len(repeats),
        tokens=input_tokens + output_tokens,
        tokens_wave1_avg=avg([result.total_tokens for result in wave1]),
        tokens_repeats_avg=avg([result.total_tokens for result in repeats]),
        llm_seconds_avg=avg([result.llm_ms / 1000 for result in core]),
        meko_calls_avg=avg([result.meko_calls for result in core]),
        reused=sum(result.source == "reused" for result in core),
        escalated=sum(result.escalated for result in core),
        false_reuse=sum(result.false_reuse for result in core),
        human_touches=sum(result.needs_human for result in core),
        injection_safe=sum(bool(result.injection_safe) for result in injections),
        injections=len(injections),
        cost_usd=round(input_tokens / 1e6 * INPUT_USD_PER_MTOK + output_tokens / 1e6 * OUTPUT_USD_PER_MTOK, 2),
        lookalikes=len(lookalikes),
        correct_lookalikes=sum(result.correct for result in lookalikes),
        reused_lookalikes=sum(result.source == "reused" for result in lookalikes),
        false_reuse_lookalikes=sum(result.false_reuse for result in lookalikes),
    )


def summaries_for(runs: Sequence[BenchmarkRun]) -> list[ArmSummary]:
    """All runs' results in one query."""
    total = len(ground_truth().tickets)
    by_run: dict[int, list[BenchmarkResult]] = {run.pk: [] for run in runs}
    for result in BenchmarkResult.objects.filter(run__in=runs):
        by_run[result.run_id].append(result)
    return [summarize(run, total, by_run[run.pk]) for run in runs]


def calibration_for(run: BenchmarkRun):
    observations = [
        CandidateScores(result.best_same_issue_score, result.best_other_issue_score)
        for result in run.results.filter(error="")
    ]
    rows = calibrate(observations)
    return rows, best_threshold_range(rows, observations)


def build_report(runs: Sequence[BenchmarkRun], *, generated_at: str) -> tuple[str, dict[str, Any]]:
    truth = ground_truth()
    tickets_by_ref = {ticket.ref: ticket for ticket in truth.tickets}
    summaries = summaries_for(runs)
    data: dict[str, Any] = {"app": "dejafix", "kind": "benchmark_report", "generated_at": generated_at, "arms": [s.__dict__ for s in summaries]}
    runs_per_arm = Counter(run.arm for run in runs)
    repetitions = (
        "one run per arm (no repetitions)" if max(runs_per_arm.values(), default=1) == 1
        else ", ".join(f"{count} runs of {arm}" for arm, count in sorted(runs_per_arm.items()))
    )
    lines = [
        "# DejaFix benchmark",
        "",
        f"Generated {generated_at}. Model `claude-opus-5-5`; {repetitions}.",
        "",
        "Both arms run the same agents, ticket order, prompts and documentation, each in its own fresh Meko",
        "datapack. The only difference is shared memory: approved fixes and human corrections.",
        "",
        "- **Correct:** the fix mentions every ground-truth keyword of its issue and was not escalated.",
        "- **Human touches:** tickets a person must handle (new fixes to review, or escalations).",
        "- **Simulated reviewer (Meko arm):** approves complete fixes and, when it rejects one, names the missing",
        "  keywords. Its notes come from the ground truth, so it helps more than a hurried human might.",
        "- **Lookalikes (wave 3):** tickets that read like one issue but are caused by another, run after the 36",
        "  core tickets. They test whether an approved fix is reused for the wrong problem. They are counted only",
        "  in the lookalike rows, tickets done and total tokens; every other row covers the core tickets.",
        "",
    ]
    if any(s.tickets < s.total for s in summaries):
        lines += ["> **Partial run:** some arms have not processed every ticket yet.", ""]
    header = "| Metric | " + " | ".join(f"{s.run} ({s.arm})" for s in summaries) + " |"
    lines += [header, "|---|" + "---|" * len(summaries)]

    def row(label: str, render: Callable[[ArmSummary], str]) -> None:
        lines.append(f"| {label} | " + " | ".join(render(s) for s in summaries) + " |")

    row("Tickets done", lambda s: f"{s.tickets}/{s.total}" + (f" ({s.errors} failed)" if s.errors else ""))
    row("Correct, first occurrences and repeats", lambda s: f"{s.correct}/{s.core}")
    row("Correct, first occurrences", lambda s: f"{s.correct_wave1}/{s.wave1}")
    row("Correct, repeats", lambda s: f"{s.correct_repeats}/{s.repeats}")
    row("Correct, lookalikes", lambda s: f"{s.correct_lookalikes}/{s.lookalikes}" if s.lookalikes else "-")
    row("Lookalikes that reused the right fix", lambda s: str(s.reused_lookalikes - s.false_reuse_lookalikes) if s.lookalikes else "-")
    row("Lookalikes that reused another issue's fix", lambda s: str(s.false_reuse_lookalikes) if s.lookalikes else "-")
    row("Avg tokens, first occurrences", lambda s: f"{s.tokens_wave1_avg:,.0f}")
    row("Avg tokens, repeats", lambda s: f"{s.tokens_repeats_avg:,.0f}")
    row("Avg model time per ticket", lambda s: f"{s.llm_seconds_avg:.1f} s")
    row("Avg Meko calls per ticket", lambda s: f"{s.meko_calls_avg:.1f}")
    row("Escalated to a human", lambda s: str(s.escalated))
    row("Reused an approved fix", lambda s: str(s.reused))
    row("False reuse (another issue's fix)", lambda s: str(s.false_reuse))
    row("Human touches", lambda s: f"{s.human_touches}/{s.core}")
    row("Injection tickets escalated, nothing stored", lambda s: f"{s.injection_safe}/{s.injections}")
    row("Total tokens / est. cost", lambda s: f"{s.tokens:,} / ${s.cost_usd:.2f}")

    memory_runs = [run for run in runs if run.uses_memory]
    for run in memory_runs:
        rows, best = calibration_for(run)
        data[f"calibration_{run.name}"] = [r.__dict__ for r in rows]
        lines += [
            "",
            f"## Reuse threshold calibration ({run.name})",
            "",
            "For each threshold: how many tickets would have been offered an approved fix for the reuse check,",
            "and how many of those offers were the same issue. The LLM reuse check still decides after the offer.",
            "",
            "| Threshold | Offered | Same issue | Precision | Recall |",
            "|---|---|---|---|---|",
        ]
        for r in rows:
            precision = "-" if r.precision is None else f"{r.precision:.2f}"
            recall = "-" if r.recall is None else f"{r.recall:.2f}"
            lines.append(f"| {r.threshold} | {r.offered} | {r.offered_same_issue} | {precision} | {recall} |")
        lines += ["", _threshold_verdict(best)]

    lines += ["", "## Per ticket", "", "| Run | Ticket | Issue | Wave | Source | Correct | Missing | Tokens | Decision |", "|---|---|---|---|---|---|---|---|---|"]
    for run in runs:
        for result in run.results.all():
            status = result.error[:40] if result.error else ("yes" if result.correct else "no")
            ticket = tickets_by_ref.get(result.ticket_ref)
            issue = f"{result.issue} (looks like {ticket.lookalike_of})" if ticket and ticket.lookalike_of else result.issue
            lines.append(
                f"| {run.name} | {result.ticket_ref} | {issue} | {result.wave} | {result.source or '-'} | {status} | "
                f"{', '.join(result.missing_keywords) or '-'} | {result.total_tokens:,} | {result.human_decision or ('escalated' if result.escalated else '-')} |"
            )
    return "\n".join(lines) + "\n", data


def _threshold_verdict(best) -> str:
    if best is None:
        return "Not enough data to compare thresholds yet."
    text = (
        f"Thresholds {best.low}-{best.high} tie for the best result (no other issue's fix offered, recall {best.recall:.2f}). "
        f"The current setting, {REUSE_MIN_RERANK_SCORE}, is {'inside this range, so it is kept' if best.contains(REUSE_MIN_RERANK_SCORE) else 'outside this range'}."
    )
    if not best.precision_tested:
        text += " No other issue's fix was offered at any threshold, so this data does not test how low the threshold can safely go."
    return text


def store_report_artifact(data: Mapping[str, Any], *, session_factory: SessionFactory | None = None) -> str:
    """Keep the report's numbers in the main datapack as a Meko artifact."""
    factory = session_factory or default_session_factory
    calls: list[MekoCallRecord] = []
    content = json.dumps(data, indent=2, sort_keys=True, default=str).encode("utf-8")

    async def store() -> str:
        async with factory(MekoConfig.from_settings(), calls) as root:
            meko = await AgentTrace(root, agent=BENCHMARK_AGENT, ticket_ref="report").open()
            return await put_artifact(meko, filename="dejafix-benchmark-report.json", content=content, content_type=JSON_CONTENT_TYPE)

    return async_to_sync(store)()
