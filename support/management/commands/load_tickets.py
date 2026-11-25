"""Load the synthetic tickets into the local database (idempotent).

Usage: `python manage.py load_tickets [--file data/tickets.json] [--reset]`
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from support.models import MAX_BODY_LENGTH, MAX_SUBJECT_LENGTH, AgentRun, Resolution, Ticket

DEFAULT_FILE = Path("data") / "tickets.json"
EXTERNAL_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
REQUIRED_FIELDS = ("external_id", "subject", "body", "customer")


def validate_ticket(raw: Any, index: int) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise CommandError(f"tickets[{index}] must be an object")
    missing = [name for name in REQUIRED_FIELDS if not isinstance(raw.get(name), str) or not raw[name].strip()]
    if missing:
        raise CommandError(f"tickets[{index}] is missing {missing}")
    if not EXTERNAL_ID.match(raw["external_id"]):
        raise CommandError(f"tickets[{index}].external_id must match {EXTERNAL_ID.pattern}")
    if len(raw["subject"]) > MAX_SUBJECT_LENGTH or len(raw["body"]) > MAX_BODY_LENGTH:
        raise CommandError(f"tickets[{index}] subject or body is too long")
    return {
        "external_id": raw["external_id"],
        "subject": raw["subject"].strip(),
        "body": raw["body"].strip(),
        "customer": raw["customer"].strip()[:120],
        "expected_issue": str(raw.get("expected_issue", ""))[:40],
    }


class Command(BaseCommand):
    help = "Load synthetic tickets from JSON. Existing tickets keep their status unless --reset is given."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Set the loaded tickets back to NEW and delete their local runs and resolutions (Meko is untouched).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path: Path = options["file"]
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CommandError(f"Cannot read {path}: {exc}") from exc
        raw_tickets = document.get("tickets") if isinstance(document, dict) else None
        if not isinstance(raw_tickets, list) or not raw_tickets:
            raise CommandError(f"{path} has no 'tickets' list")
        tickets = [validate_ticket(raw, index) for index, raw in enumerate(raw_tickets)]
        refs = [ticket["external_id"] for ticket in tickets]
        if len(set(refs)) != len(refs):
            raise CommandError("external_id values must be unique")

        with transaction.atomic():
            existing = set(Ticket.objects.filter(external_id__in=refs).values_list("external_id", flat=True))
            Ticket.objects.bulk_create([Ticket(**ticket) for ticket in tickets if ticket["external_id"] not in existing])
            if options["reset"]:
                Resolution.objects.filter(ticket__external_id__in=refs).delete()
                AgentRun.objects.filter(ticket__external_id__in=refs).delete()
                Ticket.objects.filter(external_id__in=refs).update(status=Ticket.Status.NEW, failure_reason="")

        created = len(tickets) - len(existing)
        reset_note = ", all reset to NEW" if options["reset"] else ""
        self.stdout.write(self.style.SUCCESS(f"{created} created, {len(existing)} already present{reset_note}."))
