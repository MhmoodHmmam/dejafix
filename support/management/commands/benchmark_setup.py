"""Create a benchmark run: its own Meko datapack, seeded with the same documentation.

Usage: `python manage.py benchmark_setup B1 --arm meko [--budget 250000]`
"""

from __future__ import annotations

import re
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser

from dejafix.meko import MekoError
from support.benchmark import check_documents, create_datapack, mark_ready
from support.models import BenchmarkRun

RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,19}$")
DEFAULT_BUDGET = 250_000
MAX_BUDGET = 1_000_000


class Command(BaseCommand):
    help = "Create a benchmark run in a fresh Meko datapack with the documentation uploaded."

    session_factory = None  # tests swap in a fake Meko

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("name", help="Run name, e.g. B1 (letters, digits, hyphens; up to 20).")
        parser.add_argument("--arm", choices=[arm.value for arm in BenchmarkRun.Arm], required=True)
        parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"Token budget (1-{MAX_BUDGET}).")
        parser.add_argument("--skip-seed", action="store_true", help="Don't upload documents (tests only).")

    def handle(self, *args: Any, **options: Any) -> None:
        name, arm, budget = options["name"], options["arm"], options["budget"]
        if not RUN_NAME.match(name):
            raise CommandError("Run name must be letters, digits or hyphens (up to 20).")
        if not 1 <= budget <= MAX_BUDGET:
            raise CommandError(f"--budget must be between 1 and {MAX_BUDGET}")
        run, created = BenchmarkRun.objects.get_or_create(name=name, defaults={"arm": arm, "token_budget": budget})
        if run.arm != arm:
            raise CommandError(f"Run {name} already exists as arm {run.arm}.")
        if run.status != BenchmarkRun.Status.SETUP:
            self.stdout.write(f"Run {name} is already {run.status} (datapack {run.datapack_name}).")
            return
        try:
            run = create_datapack(run, session_factory=self.session_factory)
            self.stdout.write(f"Datapack {run.datapack_name} ({run.datapack_id})")
            if not options["skip_seed"]:
                call_command("seed_knowledge", "--datapack", run.datapack_id, "--wait", stdout=self.stdout)
            chunks = check_documents(run, session_factory=self.session_factory)
        except MekoError as exc:
            raise CommandError(f"Benchmark setup failed: {exc}") from exc
        if not chunks:
            raise CommandError("The new datapack returns no documentation yet. Wait for indexing and run setup again.")
        mark_ready(run)
        self.stdout.write(self.style.SUCCESS(f"Run {name} ({arm}) is ready: {chunks} documentation chunk(s) found."))
