"""Import weak searches by our resolvers from Meko's self-learning log (read-only in Meko).

Usage: `python manage.py find_gaps`
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from dejafix.meko import MekoError
from support.gaps import import_gaps
from support.models import KnowledgeGap


class Command(BaseCommand):
    help = "Import weak searches (Meko self-learning add_context) as knowledge gaps."

    session_factory = None  # tests swap in a fake Meko

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            result = import_gaps(session_factory=self.session_factory)
        except MekoError as exc:
            raise CommandError(f"Finding gaps failed: {exc}") from exc
        quality = "unknown" if result.quality_pct is None else f"{result.quality_pct}%"
        self.stdout.write(f"Meko context quality: {quality} of recent searches found a strong match.")
        for gap in KnowledgeGap.objects.filter(status=KnowledgeGap.Status.OPEN).select_related("ticket"):
            ticket = gap.ticket.external_id if gap.ticket else "-"
            score = "-" if gap.score_before is None else f"{gap.score_before:.2f}"
            self.stdout.write(f"  [{gap.band_before:<4} {score:>4}] {ticket:<6} {gap.query[:90]}")
        self.stdout.write(self.style.SUCCESS(
            f"{result.created} new gap(s), {result.updated} updated, {result.ignored} ignored (other agents or already verified)."
        ))
