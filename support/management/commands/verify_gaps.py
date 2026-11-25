"""Replay the weak searches behind published articles (Meko `selflearning_verify`).

Each replay is a real search against your Meko quota (10 a minute), so runs are small.

Usage: `python manage.py verify_gaps [--limit 5]`
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from dejafix.meko import MekoError
from support.gaps import MAX_VERIFY_PER_RUN, verify_gaps


class Command(BaseCommand):
    help = "Check whether published articles closed their knowledge gaps."

    session_factory = None  # tests swap in a fake Meko

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=5, help=f"Max gaps to replay (1-{MAX_VERIFY_PER_RUN}).")

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options["limit"]
        if not 1 <= limit <= MAX_VERIFY_PER_RUN:
            raise CommandError(f"--limit must be between 1 and {MAX_VERIFY_PER_RUN}")
        try:
            report = verify_gaps(limit=limit, session_factory=self.session_factory)
        except MekoError as exc:
            raise CommandError(f"Verifying gaps failed: {exc}") from exc

        for row in report.checked:
            before = f"{row['band_before']} {_score(row['score_before'])}"
            after = f"{row['band_after']} {_score(row['score_after'])}"
            source = f"via {row['top_source_after']}" if row["top_source_after"] else ""
            rank = row["article_kb_rank"]
            placed = f"article #{rank} in knowledge base" if rank else "article not in top knowledge-base results"
            self.stdout.write(f"gap {row['gap']}: {before:>10} -> {after:<10} {source:<22} {placed}")
            self.stdout.write(f"         {str(row['query'])[:90]}")
        if report.indexing:
            self.stdout.write(self.style.WARNING(f"{report.indexing} article(s) still indexing; run again later."))
        if report.stopped_early:
            self.stdout.write(self.style.WARNING(report.stopped_early))
        if not report.checked and not report.indexing:
            self.stdout.write("Nothing to verify. Publish a drafted gap in the admin first.")
            return
        if report.report_hash:
            self.stdout.write(f"Report stored in Meko as artifact {report.report_hash}.")
        verified = sum(row["band_after"] == "high" for row in report.checked)
        self.stdout.write(self.style.SUCCESS(f"{verified} of {len(report.checked)} gap(s) now find a strong match."))


def _score(value: object) -> str:
    return f"{value:.2f}" if isinstance(value, float) else "-"
