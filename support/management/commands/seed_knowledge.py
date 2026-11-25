"""Upload the product documentation in data/kb/ to the Meko knowledge base.

Meko issues a presigned URL per file (`knowledgebase_upload_document`); the bytes are POSTed
there as multipart, fields first and the file last. Files Meko already has (same SHA-256)
are skipped, so re-running is safe.

Usage: `python manage.py seed_knowledge [--dir data/kb] [--wait] [--datapack <uuid>]`
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import anyio
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from dejafix.agents.knowledge import (
    FAILED_STATES,
    INDEXED_STATES,
    LIBRARIAN_AGENT,
    Uploader,
    document_state,
    post_to_presigned_url,
    upload_document,
)
from dejafix.meko import MekoConfig, MekoError, MekoGateway, MekoSession

DEFAULT_DIR = Path("data") / "kb"
SEED_SESSION_ID = "dejafix:seed-knowledge"
STATUS_POLL_SECONDS = 10
STATUS_POLL_ATTEMPTS = 12
CONTENT_TYPES = {".md": "text/markdown", ".txt": "text/plain"}


class Command(BaseCommand):
    help = "Upload data/kb/*.md to the Meko knowledge base (skips files Meko already has)."

    session_factory = MekoSession
    uploader: Uploader = staticmethod(post_to_presigned_url)

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
        parser.add_argument("--wait", action="store_true", help="Poll until Meko has indexed every new file.")
        parser.add_argument("--datapack", default="", help="Upload into this datapack instead of MEKO_DATAPACK_ID.")

    def handle(self, *args: Any, **options: Any) -> None:
        folder: Path = options["dir"]
        if not folder.is_absolute():
            folder = Path(settings.BASE_DIR) / folder
        files = sorted(path for path in folder.glob("*") if path.suffix in CONTENT_TYPES)
        if not files:
            raise CommandError(f"No .md or .txt files in {folder}")
        try:
            config = MekoConfig.from_settings()
            if options["datapack"]:
                config = replace(config, datapack_id=options["datapack"])  # re-validated as a UUID
            if not config.datapack_id:
                raise CommandError("MEKO_DATAPACK_ID is not set.")
            anyio.run(self._seed, config, files, options["wait"])
        except MekoError as exc:
            raise CommandError(f"Seeding failed: {exc}") from exc

    async def _seed(self, config: MekoConfig, files: list[Path], wait: bool) -> None:
        async with self.session_factory(config) as root:
            librarian = root.as_agent(LIBRARIAN_AGENT)
            conversation = await librarian.call("conversation_create", title="DejaFix knowledge seed", session_id=SEED_SESSION_ID)
            meko = librarian.in_conversation(conversation["id"])
            pending: list[tuple[str, str]] = []
            for path in files:
                key = await self._upload(meko, path)
                if key:
                    pending.append((path.name, key))
            if wait:
                await self._wait_for_indexing(meko, pending)

    async def _upload(self, meko: MekoGateway, path: Path) -> str | None:
        uploaded = await upload_document(
            meko,
            filename=path.name,
            content=path.read_bytes(),
            content_type=CONTENT_TYPES[path.suffix],
            uploader=self.uploader,
        )
        if uploaded.already_uploaded:
            self.stdout.write(f"  {path.name}: already in Meko, skipped")
            return None
        self.stdout.write(self.style.SUCCESS(f"  {path.name}: uploaded"))
        return uploaded.key

    async def _wait_for_indexing(self, meko: MekoGateway, pending: list[tuple[str, str]]) -> None:
        for name, key in pending:
            for _ in range(STATUS_POLL_ATTEMPTS):
                state = await document_state(meko, key)
                if state in INDEXED_STATES | FAILED_STATES:
                    self.stdout.write(f"  {name}: {state}")
                    break
                await anyio.sleep(STATUS_POLL_SECONDS)
            else:
                self.stdout.write(self.style.WARNING(f"  {name}: still indexing; check the Meko portal"))
