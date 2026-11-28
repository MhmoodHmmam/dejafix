"""The drift benchmark: does an approved fix go stale after the product changes, and does retiring it help?

Each run has its own datapack, seeded with the same documentation as the main benchmark:
1. Each case's first ticket is solved and the simulated reviewer approves it (graded by the
   keywords in data/tickets.json), so it becomes shared memory.
2. The product updates are published to the knowledge base.
3. In the "retire" arm a simulated staff member retires each old fix with the case's reason; in
   the "stale" arm nobody does.
4. The repeats run, graded by the new keywords. A new fix goes to the simulated reviewer as
   usual, so a later repeat can reuse it.

Results are written to docs/drift.json (never to the app's own tables), and the report to
docs/drift.md.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from asgiref.sync import async_to_sync
from django.conf import settings

from dejafix.agents.pipeline import (
    PipelineOutcome,
    TicketInput,
    curate_approved_fix,
    record_human_correction,
    retire_shared_fix,
    run_pipeline,
)
from dejafix.bench import BenchTicket, DriftCase, DriftRow, grade, load_drift, oracle_review, summarize_drift
from dejafix.llm import LLMError, StructuredLLM
from dejafix.meko import MekoCallRecord, MekoConfig, MekoError, MekoGateway
from support.benchmark import MAX_ERROR_LENGTH, SessionFactory, default_session_factory, ground_truth

logger = logging.getLogger("dejafix.support")

DRIFT_PATH = Path("data") / "drift.json"
STALE, RETIRE = "stale", "retire"
ARMS = (STALE, RETIRE)
FIRST, REPEAT = "first", "repeat"
MAX_SUMMARY = 160
Publish = Callable[[list[str]], None]


def cases() -> tuple[DriftCase, ...]:
    base = Path(settings.BASE_DIR)
    loaded = load_drift(json.loads((base / DRIFT_PATH).read_text(encoding="utf-8")), ground_truth())
    missing = [case.update for case in loaded if not (base / case.update).is_file()]
    if missing:
        raise ValueError(f"drift: missing product updates {missing}")
    return loaded


def curator_agent(run_name: str) -> str:
    return f"dejafix-curator-drift-{run_name.lower()}"


def reviewer_agent(run_name: str) -> str:
    return f"dejafix-human-review-drift-{run_name.lower()}"


@dataclass(frozen=True)
class _Approved:
    memory_id: str
    ticket_ref: str
    signature: str
    summary: str


def run_drift(
    name: str,
    arm: str,
    *,
    datapack_id: str,
    cases: Sequence[DriftCase],
    llm: StructuredLLM,
    publish: Publish,
    session_factory: SessionFactory | None = None,
) -> list[DriftRow]:
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}")
    factory = session_factory or default_session_factory
    config = replace(MekoConfig.from_settings(), datapack_id=datapack_id)
    curator, reviewer = curator_agent(name), reviewer_agent(name)
    rows: list[DriftRow] = []
    approved: dict[str, _Approved] = {}

    def run_ticket(case: DriftCase, ticket: BenchTicket, phase: str, retired: frozenset[str]) -> None:
        keywords = case.old_keywords if phase == FIRST else case.new_keywords
        calls: list[MekoCallRecord] = []
        bench_ref = f"{name}-{ticket.ref}"

        async def solve() -> tuple[PipelineOutcome, str, str]:
            async with factory(config, calls) as meko:
                outcome = await run_pipeline(
                    meko, llm, TicketInput(bench_ref, ticket.subject, ticket.body), resolver="a",
                    trusted_curator=curator, trusted_reviewer=reviewer, calls=calls, retired_fix_ids=retired,
                )
                decision, memory_id = await _simulated_review(meko, outcome, bench_ref, keywords, curator, reviewer)
                return outcome, decision, memory_id

        try:
            outcome, decision, memory_id = async_to_sync(solve)()
        except (MekoError, LLMError) as exc:
            error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH]
            logger.warning("Drift %s ticket %s failed: %s", name, ticket.ref, error)
            rows.append(DriftRow(name, arm, case.issue, ticket.ref, phase, "", False, False, False, False, False, 0, 0, error=error))
            return
        rows.append(_row(name, arm, case, ticket, phase, outcome, decision, keywords, approved))
        if phase == FIRST and memory_id:
            approved[case.issue] = _Approved(memory_id, bench_ref, outcome.triage.problem_signature, outcome.fix_summary)

    for case in cases:
        run_ticket(case, case.first, FIRST, frozenset())
    publish([case.update for case in cases])
    retired = frozenset(_retire_all(name, cases, approved, factory, config, reviewer)) if arm == RETIRE else frozenset()
    for case in cases:
        for ticket in case.repeats:
            run_ticket(case, ticket, REPEAT, retired)
    return rows


async def _simulated_review(
    meko: MekoGateway, outcome: PipelineOutcome, bench_ref: str, keywords: Sequence[str], curator: str, reviewer: str
) -> tuple[str, str]:
    """As in the main benchmark: every new, non-escalated fix is approved or rejected right away."""
    if outcome.source != "new" or outcome.escalated:
        return "", ""
    decision = oracle_review(grade([outcome.fix_summary, *outcome.fix_steps, outcome.customer_reply], keywords))
    if decision.approve:
        curated = await curate_approved_fix(
            meko, curator_agent=curator, ticket_ref=bench_ref, signature=outcome.triage.problem_signature,
            category=outcome.triage.category, summary=outcome.fix_summary, steps=outcome.fix_steps,
        )
        return "approved", curated.memory_id
    await record_human_correction(
        meko, ticket_ref=bench_ref, signature=outcome.triage.problem_signature, rejected_summary=outcome.fix_summary,
        note=decision.note, reviewer_agent=reviewer,
    )
    return "rejected", ""


def _retire_all(
    name: str, cases: Sequence[DriftCase], approved: Mapping[str, _Approved], factory: SessionFactory,
    config: MekoConfig, reviewer: str,
) -> set[str]:
    """The simulated staff member: retires every fix approved before the change, with the case's reason."""
    calls: list[MekoCallRecord] = []

    async def retire() -> set[str]:
        retired = set()
        async with factory(config, calls) as meko:
            for case in cases:
                fix = approved.get(case.issue)
                if fix is None:
                    continue
                await retire_shared_fix(
                    meko, ticket_ref=fix.ticket_ref, memory_id=fix.memory_id, signature=fix.signature,
                    retired_summary=fix.summary, note=case.retire_reason, reviewer_agent=reviewer,
                )
                retired.add(fix.memory_id)
        return retired

    return async_to_sync(retire)()


def _row(
    name: str, arm: str, case: DriftCase, ticket: BenchTicket, phase: str, outcome: PipelineOutcome, decision: str,
    keywords: Sequence[str], approved: Mapping[str, _Approved],
) -> DriftRow:
    graded = grade([outcome.fix_summary, *outcome.fix_steps, outcome.customer_reply], keywords)
    old_ids = {fix.memory_id for fix in approved.values()}
    retrieve = next((step.decision for step in outcome.steps if step.step == "retrieve"), {})
    usages = [step.usage for step in outcome.steps if step.usage]
    return DriftRow(
        run=name, arm=arm, issue=case.issue, ticket=ticket.ref, phase=phase, source=outcome.source,
        correct=graded.correct and not outcome.escalated,
        stale_reuse=phase == REPEAT and outcome.source == "reused" and outcome.reused_memory_id in old_ids,
        retired_skipped=bool(retrieve.get("retired_fixes_skipped")),
        escalated=outcome.escalated,
        human=outcome.source == "new" or outcome.escalated,
        tokens=sum(usage.input_tokens + usage.output_tokens for usage in usages),
        llm_ms=sum(usage.latency_ms for usage in usages),
        decision=decision, missing=tuple(graded.missing), fix_summary=outcome.fix_summary[:MAX_SUMMARY],
    )


# --- Results and report -----------------------------------------------------------------------


def row_to_dict(row: DriftRow) -> dict[str, Any]:
    data = asdict(row)
    data["missing"] = list(row.missing)
    return data


def row_from_dict(data: Mapping[str, Any]) -> DriftRow:
    return DriftRow(**{**data, "missing": tuple(data.get("missing", ()))})


def _ordered(runs: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, Mapping[str, Any]]]:
    return sorted(runs.items(), key=lambda item: (ARMS.index(item[1]["arm"]), item[0]))


def _count(runs: Sequence[tuple[str, Mapping[str, Any]]], arm: str) -> int:
    return sum(run["arm"] == arm for _, run in runs)


def build_report(runs: Mapping[str, Mapping[str, Any]], *, generated_at: str) -> tuple[str, dict[str, Any]]:
    """Only complete runs are compared: a run whose tickets failed (live: Meko's free-tier quota ran out
    halfway through S2) would count outside failures as wrong answers. It is named below the table."""
    every = _ordered(runs)
    rows = {name: [row_from_dict(row) for row in run["rows"]] for name, run in every}
    left_out = [(name, run) for name, run in every if any(row.error for row in rows[name])]
    ordered = [(name, run) for name, run in every if (name, run) not in left_out]
    summaries = {name: summarize_drift(rows[name]) for name, _ in ordered}
    firsts = {name: [row for row in rows[name] if row.phase == FIRST] for name, _ in ordered}
    total = max((summary.repeats for summary in summaries.values()), default=0)

    def cell(value: int, of: int) -> str:
        return str(value) if of == total else f"{value}/{of}"

    lines = [
        "# Drift benchmark: when an approved fix goes out of date",
        "",
        f"Generated by `manage.py drift_benchmark report` on {generated_at}. Cases: [data/drift.json](../data/drift.json).",
        "",
        "Each run has its own Meko datapack with the same documentation as the main benchmark. Each case's first",
        "ticket is solved and approved (so it becomes shared memory); then a product update is published to the",
        "knowledge base, and two repeats of the problem run, graded by the keywords of the *new* answer. In the",
        "**retire** arm a simulated staff member retires each old fix with a reason before the repeats; in the",
        "**stale** arm nobody does.",
        "",
        "| | " + " | ".join(f"{name} · {run['arm']}" for name, run in ordered) + " |",
        "|---|" + "---|" * len(ordered),
    ]

    def add(label: str, values: Sequence[str]) -> None:
        lines.append(f"| {label} | " + " | ".join(values) + " |")

    add(f"First tickets approved before the change, of {len(next(iter(firsts.values()), []))}",
        [str(sum(row.decision == "approved" for row in firsts[name])) for name, _ in ordered])
    add(f"Correct after the change, of {total}", [cell(summaries[name].correct, summaries[name].repeats) for name, _ in ordered])
    add(f"Reused the outdated fix, of {total}", [cell(summaries[name].stale_reuses, summaries[name].repeats) for name, _ in ordered])
    add("Retired fix skipped by the resolver", [str(summaries[name].retired_skipped) for name, _ in ordered])
    add(f"Needed a person, of {total}", [cell(summaries[name].humans, summaries[name].repeats) for name, _ in ordered])
    add("Avg Claude tokens per repeat", [f"{summaries[name].avg_tokens:,}" for name, _ in ordered])
    add("Failed tickets", [str(summaries[name].errors) for name, _ in ordered])
    lines += [
        "",
        "## Every repeat",
        "",
        "| Run | Ticket | Issue | Source | Correct | Outdated fix reused | Missing keywords | Fix |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name, _ in ordered:
        for row in rows[name]:
            if row.phase != REPEAT:
                continue
            fix = row.error or row.fix_summary.replace("|", "/")
            lines.append(
                f"| {name} | {row.ticket} | {row.issue} | {row.source or '-'} | {'yes' if row.correct else 'no'} | "
                f"{'yes' if row.stale_reuse else 'no'} | {', '.join(row.missing) or '-'} | {fix} |"
            )
    if left_out:
        lines += ["", "## Runs left out", ""]
        for name, run in left_out:
            failed = [row for row in rows[name] if row.error]
            lines.append(f"- {name} ({run['arm']}): {len(failed)} of {len(rows[name])} tickets failed, so it is not compared. "
                         f"First error: {failed[0].error}")
    lines += [
        "",
        "## Honest limits",
        "",
        f"- {total} repeats per run, and {_count(ordered, STALE)} complete stale run(s) against {_count(ordered, RETIRE)} complete "
        "retire run(s): a small sample, so every repeat is listed above.",
        "- The retiring staff member is simulated and acts right after the update. The benchmark measures what",
        "  happens after a retirement, not how fast a person notices the change.",
        "- The retirement reason names the new answer, as a person retiring a fix would. The product update is",
        "  in the knowledge base in both arms.",
        "- The simulated reviewer is the same as in the main benchmark: it approves a complete fix at once.",
        "",
    ]
    data = {
        "generated_at": generated_at,
        "runs": {name: {"arm": run["arm"], "summary": asdict(summaries[name])} for name, run in ordered},
        "left_out": sorted(name for name, _ in left_out),
    }
    return "\n".join(lines), data
