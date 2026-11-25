"""Draft knowledge-base articles for open gaps (calls Claude and Meko; costs tokens).

Drafts are not published: a human reviews and publishes them in the admin.

Usage: `python manage.py draft_gap_articles [--limit 5]`
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from dejafix.llm import ClaudeLLM, LLMError, StructuredLLM
from support.gaps import MAX_DRAFTS_PER_RUN, draft_gap
from support.models import KnowledgeGap


class Command(BaseCommand):
    help = "Draft articles for open knowledge gaps from human-approved fixes (calls Claude)."

    # Tests swap these for fakes.
    llm_factory = staticmethod(ClaudeLLM.from_settings)
    session_factory = None

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=5, help=f"Max gaps to draft (1-{MAX_DRAFTS_PER_RUN}).")

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options["limit"]
        if not 1 <= limit <= MAX_DRAFTS_PER_RUN:
            raise CommandError(f"--limit must be between 1 and {MAX_DRAFTS_PER_RUN}")
        gaps = list(KnowledgeGap.objects.filter(status=KnowledgeGap.Status.OPEN).order_by("id")[:limit])
        if not gaps:
            self.stdout.write("No open gaps. Run find_gaps first.")
            return
        try:
            llm: StructuredLLM = self.llm_factory()
        except LLMError as exc:
            raise CommandError(str(exc)) from exc

        tokens = 0
        for gap in gaps:
            outcome = draft_gap(gap, llm=llm, session_factory=self.session_factory)
            tokens += outcome.tokens
            if outcome.error:
                self.stdout.write(self.style.ERROR(f"gap {gap.pk}: FAILED {outcome.error}"))
            else:
                self.stdout.write(f"gap {gap.pk}: {outcome.status:<12} {gap.query[:80]}")
        self.stdout.write(self.style.SUCCESS(f"{len(gaps)} gap(s) processed, {tokens} tokens. Review drafts in the admin."))
