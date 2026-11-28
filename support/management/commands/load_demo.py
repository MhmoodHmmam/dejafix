"""Load the recorded demo (data/demo.json) so the app can be browsed without any API key.

It holds the state of the app on 2026-11-28: the 42 synthetic tickets, the real Claude + Meko runs
with every agent step, the knowledge gaps and the benchmark runs. The reviewers are two demo users
with unusable passwords; create your own login with `createsuperuser`.

Usage: `python manage.py load_demo [--force]`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError, CommandParser

from support.models import Ticket

DEMO_FIXTURE = Path("data") / "demo.json"


class Command(BaseCommand):
    help = "Load the recorded demo so the app can be browsed without API keys."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--force", action="store_true", help="Load even if the database has tickets (rows with the same ids are replaced)."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        if Ticket.objects.exists() and not options["force"]:
            raise CommandError("The database already has tickets. Use a fresh database, or --force to overwrite them.")
        call_command("loaddata", str(Path(settings.BASE_DIR) / DEMO_FIXTURE), verbosity=0)
        self.stdout.write(self.style.SUCCESS(f"Loaded the recorded demo: {Ticket.objects.count()} tickets."))
        self.stdout.write("Next: python manage.py createsuperuser, then python manage.py runserver --insecure")
