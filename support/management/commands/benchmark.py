"""Run a benchmark arm over the tickets, in order (calls Claude and Meko; costs tokens).

Re-running continues where the last run stopped; failed tickets are retried.

Usage: `python manage.py benchmark B1 [--limit 6]`
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from dejafix.llm import ClaudeLLM, LLMError, StructuredLLM
from support.benchmark import run_benchmark, spent_tokens
from support.models import BenchmarkRun

MAX_TICKETS = 36


class Command(BaseCommand):
    help = "Run a benchmark arm over the next tickets (calls Claude and Meko)."

    # Tests swap these for fakes.
    llm_factory = staticmethod(ClaudeLLM.from_settings)
    session_factory = None

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("name")
        parser.add_argument("--limit", type=int, default=MAX_TICKETS, help=f"Max tickets this time (1-{MAX_TICKETS}).")

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options["limit"]
        if not 1 <= limit <= MAX_TICKETS:
            raise CommandError(f"--limit must be between 1 and {MAX_TICKETS}")
        run = BenchmarkRun.objects.filter(name=options["name"]).first()
        if run is None:
            raise CommandError(f"Unknown run {options['name']}. Create it with benchmark_setup.")
        if run.status not in (BenchmarkRun.Status.READY, BenchmarkRun.Status.RUNNING):
            raise CommandError(f"Run {run.name} is {run.status}.")
        try:
            llm: StructuredLLM = self.llm_factory()
        except LLMError as exc:
            raise CommandError(str(exc)) from exc

        progress = run_benchmark(run, llm=llm, limit=limit, session_factory=self.session_factory)
        for result in run.results.order_by("-id")[: progress.ran][::-1]:
            label = "correct" if result.correct else ("ESCALATED" if result.escalated else "WRONG")
            outcome = result.error or f"{result.source:<6} {label:<9} missing={result.missing_keywords}"
            self.stdout.write(f"{result.ticket_ref} w{result.wave} tokens={result.total_tokens:>6}  {outcome}")
        if progress.stopped_by_budget:
            self.stdout.write(self.style.WARNING(f"Stopped: token budget {run.token_budget:,} reached."))
        done = run.results.filter(error="").count()
        self.stdout.write(self.style.SUCCESS(
            f"{run.name}: {progress.ran} ran ({progress.failed} failed), {done}/36 done, {spent_tokens(run):,} tokens so far."
        ))
