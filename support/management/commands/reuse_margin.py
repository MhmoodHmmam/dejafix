"""Measure how far below the reuse threshold another issue's approved fix stays (read-only).

For every benchmark ticket, search the knowledge base of a finished Meko run with the ticket's own
text (subject and body), and record the best score of an approved fix for the ticket's real issue
and for any other issue. The six lookalike tickets are worded like another issue on purpose, so they
put the most pressure on the threshold. Writes docs/reuse-margin.md.

It lists the run's collective memory (`memory_get_all`) and searches (`knowledgebase_search`); the
only write is the conversation Meko requires for any call. Meko's listing also names who promoted
each memory: those fields are never read.

Usage: `python manage.py reuse_margin M3 [--output docs/reuse-margin.md]`
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from dejafix.agents.retrieval import REUSE_MIN_RERANK_SCORE
from dejafix.agents.trace import AgentTrace
from dejafix.bench import MarginRow, reuse_margin
from dejafix.meko import MekoError
from support.benchmark import default_session_factory, ground_truth, run_config
from support.models import BenchmarkRun

OUTPUT_PATH = Path("docs") / "reuse-margin.md"
MARGIN_AGENT = "dejafix-benchmark"
PAGE_SIZE = 100
MAX_PAGES = 20
SEARCH_LIMIT = 20
PROMOTED_SOURCE = "memory"


class Command(BaseCommand):
    help = "Measure the margin between the reuse threshold and the closest wrong fix (read-only)."

    session_factory = None  # tests swap in a fake Meko

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("name", help="A finished Meko-arm benchmark run, e.g. M3")
        parser.add_argument("--output", type=Path, default=OUTPUT_PATH)

    def handle(self, *args: Any, **options: Any) -> None:
        run = BenchmarkRun.objects.filter(name=options["name"]).first()
        if run is None:
            raise CommandError(f"Unknown run {options['name']}.")
        if not run.uses_memory:
            raise CommandError(f"Run {run.name} is docs-only: it has no approved fixes.")
        issue_by_memory = dict(run.results.exclude(promoted_memory_id="").values_list("promoted_memory_id", "issue"))
        if not issue_by_memory:
            raise CommandError(f"Run {run.name} has no approved fixes.")
        try:
            rows = async_to_sync(self._measure)(run, issue_by_memory)
        except MekoError as exc:
            raise CommandError(f"Meko failed: {exc}") from exc
        output: Path = options["output"]
        if not output.is_absolute():
            output = Path(settings.BASE_DIR) / output
        markdown = self._markdown(run, rows)
        output.write_text(markdown, encoding="utf-8")
        self.stdout.write(markdown)
        self.stdout.write(self.style.SUCCESS(f"Written to {output}."))

    async def _measure(self, run: BenchmarkRun, issue_by_memory: Mapping[str, str]) -> list[MarginRow]:
        factory = self.session_factory or default_session_factory
        async with factory(run_config(run), []) as meko:
            scoped = await AgentTrace(meko, agent=MARGIN_AGENT, ticket_ref=f"MARGIN-{run.name}").open()
            in_memory = 0
            for page in range(1, MAX_PAGES + 1):
                reply = await scoped.call("memory_get_all", promoted=True, page=page, page_size=PAGE_SIZE)
                for row in reply.get("promoted_memories") or [] if isinstance(reply, dict) else []:
                    in_memory += row.get("agent_id") == run.curator_agent and str(row.get("id") or "") in issue_by_memory
                if not isinstance(reply, dict) or page >= int(reply.get("total_pages") or 1):
                    break
            self.fixes_in_memory = in_memory
            rows = []
            for ticket in ground_truth().tickets:
                issue = ticket.issue
                found = await scoped.call("knowledgebase_search", query=f"{ticket.subject}\n{ticket.body}", limit=SEARCH_LIMIT)
                same: float | None = None
                other: float | None = None
                other_name = ""
                for hit in found.get("results") or [] if isinstance(found, dict) else []:
                    filters = hit.get("metadata_filters") if isinstance(hit.get("metadata_filters"), Mapping) else {}
                    hit_issue = issue_by_memory.get(str(filters.get("memory_id") or ""))
                    score = hit.get("rerank_score")
                    if filters.get("source") != PROMOTED_SOURCE or hit_issue is None or not isinstance(score, (int, float)):
                        continue
                    if hit_issue == issue:
                        same = max(same or 0.0, float(score))
                    elif other is None or score > other:
                        other, other_name = float(score), hit_issue
                rows.append(MarginRow(issue=issue, same_issue=same, other_issue=other, other_issue_name=other_name,
                                      ticket=ticket.ref, lookalike_of=ticket.lookalike_of))
            return rows

    def _markdown(self, run: BenchmarkRun, rows: list[MarginRow]) -> str:
        summary = reuse_margin(rows, REUSE_MIN_RERANK_SCORE)

        def fmt(score: float | None) -> str:
            return "-" if score is None else f"{score:.3f}"

        lines = [
            "# Reuse threshold margin",
            "",
            f"Generated {timezone.now():%Y-%m-%d %H:%M} UTC by `manage.py reuse_margin {run.name}` (read-only).",
            "",
            f"Every benchmark ticket's text (subject and body) was searched in {run.name}'s knowledge base, which",
            f"holds its {getattr(self, 'fixes_in_memory', 0)} human-approved fixes. The table shows the best score of a fix for the ticket's real",
            "issue and for any other issue; \"-\" means none came back in the top "
            f"{SEARCH_LIMIT}. A fix is offered for reuse at {REUSE_MIN_RERANK_SCORE} or more, and a cheap check still decides.",
            "Lookalikes (marked) are worded like another issue on purpose.",
            "",
            "| Ticket | Real issue | Same-issue fix | Best other-issue fix | Other issue |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {row.ticket}{' (lookalike of ' + row.lookalike_of + ')' if row.lookalike_of else ''} | {row.issue} | "
            f"{fmt(row.same_issue)} | {fmt(row.other_issue)} | {row.other_issue_name or '-'} |"
            for row in rows
        ]
        lines += [
            "",
            f"- Highest score of another issue's fix: {fmt(summary.max_other)}"
            + (f" (threshold {REUSE_MIN_RERANK_SCORE}, margin {summary.margin:.3f})." if summary.margin is not None else "."),
            f"- Lowest score of the same issue's fix: {fmt(summary.min_same)}.",
            f"- Other-issue fixes at or above the threshold: {summary.over_threshold} of {len(rows)} searches.",
            f"- Tickets searched: {len(rows)}; one run's datapack. Raw ticket text carries more of the misleading words than",
            "  the English problem signature the resolver really searches with, so this is the stricter test.",
        ]
        lines += self._recorded_section(run, fmt)
        return "\n".join(lines) + "\n"

    def _recorded_section(self, run: BenchmarkRun, fmt) -> list[str]:
        """What the resolver really saw: the scores recorded during the run, searched by triage signature."""
        results = list(run.results.filter(error="").order_by("ticket_ref"))
        others = [result.best_other_issue_score for result in results if result.best_other_issue_score is not None]
        lookalikes = [result for result in results if result.wave == 3]
        lines = [
            "",
            "## What the resolver searched with during the run",
            "",
            f"During {run.name} each ticket was searched by its triage signature, and the best approved-fix scores were",
            "recorded (same issue, other issue). The reuse check then decided.",
            "",
            f"- Tickets where another issue's fix came back at all: {len(others)} of {len(results)}.",
            f"- Highest such score: {fmt(max(others, default=None))}; at or above the threshold: "
            f"{sum(1 for score in others if score >= REUSE_MIN_RERANK_SCORE)}.",
            f"- Wrong fixes actually reused: {sum(result.false_reuse for result in results)}.",
        ]
        if lookalikes:
            lines += ["", "| Lookalike | Same-issue fix | Other-issue fix | Reused | Correct |", "|---|---|---|---|---|"]
            lines += [
                f"| {result.ticket_ref} | {fmt(result.best_same_issue_score)} | {fmt(result.best_other_issue_score)} | "
                f"{'yes' if result.source == 'reused' else 'no'}{' (wrong issue)' if result.false_reuse else ''} | "
                f"{'yes' if result.correct else 'no'} |"
                for result in lookalikes
            ]
        return lines
