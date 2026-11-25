"""Write docs/benchmark.md from benchmark runs and keep its numbers in Meko as an artifact.

Usage: `python manage.py benchmark_report B1 B2 [--no-artifact]`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from dejafix.meko import MekoError
from support.benchmark import build_report, store_report_artifact
from support.models import BenchmarkRun

REPORT_PATH = Path("docs") / "benchmark.md"


class Command(BaseCommand):
    help = "Compare benchmark runs and write docs/benchmark.md."

    session_factory = None  # tests swap in a fake Meko

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("names", nargs="+")
        parser.add_argument("--no-artifact", action="store_true", help="Don't store the report in Meko.")
        parser.add_argument("--output", type=Path, default=REPORT_PATH)

    def handle(self, *args: Any, **options: Any) -> None:
        found = {run.name: run for run in BenchmarkRun.objects.filter(name__in=options["names"])}
        missing = [name for name in options["names"] if name not in found]
        if missing:
            raise CommandError(f"Unknown run(s): {', '.join(missing)}")
        runs = [found[name] for name in options["names"]]
        markdown, data = build_report(runs, generated_at=f"{timezone.now():%Y-%m-%d %H:%M} UTC")
        output: Path = options["output"]
        if not output.is_absolute():
            output = Path(settings.BASE_DIR) / output
        output.write_text(markdown, encoding="utf-8")
        self.stdout.write(markdown)
        if options["no_artifact"]:
            return
        try:
            content_hash = store_report_artifact(data, session_factory=self.session_factory)
        except MekoError as exc:
            raise CommandError(f"Report written to {output}, but storing it in Meko failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"Written to {output}; stored in Meko as artifact {content_hash}."))
