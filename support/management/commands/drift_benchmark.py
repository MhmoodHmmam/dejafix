"""The drift benchmark (calls Claude and Meko; costs tokens): see support/drift.py.

Usage:
  python manage.py drift_benchmark run S1 --arm stale     # a fresh datapack, 3 first tickets, 6 repeats
  python manage.py drift_benchmark run R1 --arm retire
  python manage.py drift_benchmark report                  # docs/drift.md again, and the numbers in Meko
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from dejafix.llm import ClaudeLLM
from dejafix.meko import MekoError
from support import drift
from support.benchmark import count_documents, new_datapack, store_report_artifact
from support.management.commands.benchmark_setup import RUN_NAME
from support.views import missing_agent_keys

RESULTS_FILE = "drift.json"
REPORT_FILE = "drift.md"


def publish_updates(datapack_id: str, paths: list[str]) -> None:
    """Upload the product updates to the run's datapack and wait until Meko has indexed them."""
    call_command("seed_knowledge", "--datapack", datapack_id, "--wait", *(f"--file={path}" for path in paths))


class Command(BaseCommand):
    help = "Measure whether approved fixes go stale after a product change, with and without retiring them."

    # Tests swap these for fakes.
    llm_factory = staticmethod(ClaudeLLM.from_settings)
    session_factory = None
    publisher = staticmethod(publish_updates)
    results_dir = Path(settings.BASE_DIR) / "docs"

    def add_arguments(self, parser: CommandParser) -> None:
        sub = parser.add_subparsers(dest="action", required=True)
        run = sub.add_parser("run", help="Run one arm in a fresh datapack.")
        run.add_argument("name", help="Run name, e.g. S1 (letters, digits, hyphens; up to 20).")
        run.add_argument("--arm", choices=drift.ARMS, required=True)
        run.add_argument("--skip-seed", action="store_true", help="Don't upload the documentation (tests only).")
        sub.add_parser("report", help="Write docs/drift.md again and keep its numbers in Meko.")

    def handle(self, *args: Any, **options: Any) -> None:
        if options["action"] == "run":
            self.run(options["name"], options["arm"], skip_seed=options["skip_seed"])
        else:
            self.report(store=True)

    def run(self, name: str, arm: str, *, skip_seed: bool) -> None:
        if not RUN_NAME.match(name):
            raise CommandError("Run name must be letters, digits or hyphens (up to 20).")
        if missing := missing_agent_keys():
            raise CommandError(f"The drift benchmark needs {' and '.join(missing)}.")
        results = self.load()
        if name in results["runs"]:
            raise CommandError(f"Run {name} already exists in {RESULTS_FILE}; pick another name.")
        cases = drift.cases()
        try:
            datapack_id, datapack_name = new_datapack("dejafix-drift", name, arm, session_factory=self.session_factory)
            self.stdout.write(f"Datapack {datapack_name}")
            if not skip_seed:
                call_command("seed_knowledge", "--datapack", datapack_id, "--wait", stdout=self.stdout)
            if not count_documents(datapack_id, name, session_factory=self.session_factory):
                raise CommandError("The new datapack returns no documentation yet.")
            rows = drift.run_drift(
                name, arm, datapack_id=datapack_id, cases=cases, llm=self.llm_factory(),
                publish=lambda paths: self.publisher(datapack_id, paths), session_factory=self.session_factory,
            )
        except MekoError as exc:
            raise CommandError(f"Drift run failed: {exc}") from exc
        results["runs"][name] = {
            "arm": arm, "datapack_name": datapack_name, "finished_at": f"{timezone.now():%Y-%m-%d %H:%M} UTC",
            "rows": [drift.row_to_dict(row) for row in rows],
        }
        self.save(results)
        summary = drift.summarize_drift(rows)
        self.stdout.write(self.style.SUCCESS(
            f"{name} ({arm}): {summary.correct}/{summary.repeats} correct after the change, "
            f"{summary.stale_reuses} reused the outdated fix, {summary.errors} failed."
        ))
        self.report(store=False)

    def report(self, *, store: bool) -> None:
        results = self.load()
        if not results["runs"]:
            raise CommandError(f"No runs in {RESULTS_FILE} yet.")
        markdown, data = drift.build_report(results["runs"], generated_at=f"{timezone.now():%Y-%m-%d %H:%M} UTC")
        self.write(REPORT_FILE, markdown + "\n")
        self.stdout.write(f"Wrote {self.results_dir / REPORT_FILE}")
        if store:
            try:
                artifact = store_report_artifact(data, session_factory=self.session_factory, filename="dejafix-drift-report.json")
            except MekoError as exc:
                raise CommandError(f"The report was written, but storing it in Meko failed: {exc}") from exc
            self.stdout.write(f"Stored in Meko as artifact {artifact}")

    def load(self) -> dict[str, Any]:
        path = self.results_dir / RESULTS_FILE
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"runs": {}}

    def save(self, results: dict[str, Any]) -> None:
        self.write(RESULTS_FILE, json.dumps(results, indent=1, ensure_ascii=False, sort_keys=True) + "\n")

    def write(self, filename: str, text: str) -> None:
        # Written next to the target, then renamed, so an interrupted run never leaves half a file.
        path = self.results_dir / filename
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
