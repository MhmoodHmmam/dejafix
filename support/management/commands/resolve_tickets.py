"""Run the DejaFix agents on tickets, in order. Calls Claude and Meko (costs tokens).

Usage:
  python manage.py resolve_tickets T001 T013          # specific tickets
  python manage.py resolve_tickets --limit 5          # the next 5 NEW/FAILED tickets
  python manage.py resolve_tickets --resolver b       # a | b | alternate (default)
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from dejafix.llm import ClaudeLLM, LLMError, StructuredLLM
from support.models import AgentRun, Ticket
from support.services import resolve_ticket

MAX_TICKETS_PER_RUN = 50


class Command(BaseCommand):
    help = "Resolve tickets with the DejaFix agents (calls Claude and Meko)."

    # Tests swap these for fakes.
    llm_factory = staticmethod(ClaudeLLM.from_settings)
    session_factory = None

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("refs", nargs="*", help="Ticket external ids, e.g. T001.")
        parser.add_argument("--limit", type=int, default=5, help=f"Max tickets when no ids are given (1-{MAX_TICKETS_PER_RUN}).")
        parser.add_argument("--resolver", choices=["a", "b", "alternate"], default="alternate")

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options["limit"]
        if not 1 <= limit <= MAX_TICKETS_PER_RUN:
            raise CommandError(f"--limit must be between 1 and {MAX_TICKETS_PER_RUN}")
        tickets = self._select(options["refs"], limit)
        try:
            llm: StructuredLLM = self.llm_factory()
        except LLMError as exc:
            raise CommandError(str(exc)) from exc

        totals = {"tokens": 0, "reused": 0, "failed": 0}
        for index, ticket in enumerate(tickets):
            resolver = options["resolver"] if options["resolver"] != "alternate" else "ab"[index % 2]
            result = resolve_ticket(ticket, llm=llm, resolver=resolver, session_factory=self.session_factory)
            if not result.ran:
                self.stdout.write(f"{ticket.external_id}: skipped ({ticket.status})")
                continue
            if result.error:
                totals["failed"] += 1
                self.stdout.write(self.style.ERROR(f"{ticket.external_id}: FAILED {result.error}"))
                continue
            runs = AgentRun.objects.filter(ticket=result.ticket)
            tokens = sum(run.total_tokens for run in runs)
            latency = sum(run.latency_ms for run in runs)
            resolution = result.ticket.resolution
            totals["tokens"] += tokens
            totals["reused"] += resolution.source == "reused"
            self.stdout.write(
                f"{ticket.external_id}: {resolution.source:<6} {result.ticket.status:<15} "
                f"resolver={resolver} tokens={tokens:>6} llm_ms={latency:>6}  {resolution.fix_summary[:70]}"
            )
        self.stdout.write(self.style.SUCCESS(
            f"{len(tickets)} ticket(s): {totals['reused']} reused, {totals['failed']} failed, {totals['tokens']} tokens."
        ))

    @staticmethod
    def _select(refs: list[str], limit: int) -> list[Ticket]:
        if refs:
            found = {ticket.external_id: ticket for ticket in Ticket.objects.filter(external_id__in=refs)}
            missing = [ref for ref in refs if ref not in found]
            if missing:
                raise CommandError(f"Unknown ticket(s): {', '.join(missing)}. Run load_tickets first.")
            return [found[ref] for ref in refs]
        return list(Ticket.objects.filter(status__in=[Ticket.Status.NEW, Ticket.Status.FAILED]).order_by("id")[:limit])
