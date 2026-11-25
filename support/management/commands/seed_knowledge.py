"""Upload the product documentation in data/kb/ to the Meko knowledge base.

Meko issues a presigned URL per file (`knowledgebase_upload_document`); the bytes are POSTed
there as multipart, fields first and the file last. Files Meko already has (same SHA-256)
are skipped, so re-running is safe.

Usage: `python manage.py seed_knowledge [--dir data/kb] [--wait]`
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx2
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from dejafix.meko import MekoConfig, MekoError, MekoGateway, MekoSession, MekoToolError

DEFAULT_DIR = Path("data") / "kb"
LIBRARIAN_AGENT = "dejafix-librarian"
SEED_SESSION_ID = "dejafix:seed-knowledge"
HEAD_BYTES = 3072  # Meko sniffs the first 3072 bytes against the extension
UPLOAD_TIMEOUT_SECONDS = 30.0
STATUS_POLL_SECONDS = 10
STATUS_POLL_ATTEMPTS = 12
CONTENT_TYPES = {".md": "text/markdown", ".txt": "text/plain"}

Uploader = Callable[[str, Mapping[str, str], str, bytes, str], Awaitable[int]]


async def post_to_presigned_url(url: str, fields: Mapping[str, str], filename: str, content: bytes, content_type: str) -> int:
    # The URL comes from Meko, but it receives our document: only ever send it over HTTPS.
    if urlsplit(url).scheme != "https":
        raise MekoToolError("knowledgebase_upload_document", "insecure_upload_url", "upload URL is not https")
    async with httpx2.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as client:
        response = await client.post(url, data=dict(fields), files={"file": (filename, content, content_type)})
    return response.status_code


class Command(BaseCommand):
    help = "Upload data/kb/*.md to the Meko knowledge base (skips files Meko already has)."

    session_factory = MekoSession
    uploader: Uploader = staticmethod(post_to_presigned_url)

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
        parser.add_argument("--wait", action="store_true", help="Poll until Meko has indexed every new file.")

    def handle(self, *args: Any, **options: Any) -> None:
        folder: Path = options["dir"]
        if not folder.is_absolute():
            folder = Path(settings.BASE_DIR) / folder
        files = sorted(path for path in folder.glob("*") if path.suffix in CONTENT_TYPES)
        if not files:
            raise CommandError(f"No .md or .txt files in {folder}")
        try:
            config = MekoConfig.from_settings()
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
        content = path.read_bytes()
        reply = await meko.call(
            "knowledgebase_upload_document",
            filename=path.name,
            size=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            content_head_base64=base64.b64encode(content[:HEAD_BYTES]).decode("ascii"),
        )
        if reply.get("already_uploaded"):
            self.stdout.write(f"  {path.name}: already in Meko, skipped")
            return None
        status = await self.uploader(reply["url"], reply.get("fields") or {}, path.name, content, CONTENT_TYPES[path.suffix])
        if not 200 <= status < 300:
            raise MekoToolError("knowledgebase_upload_document", "upload_failed", f"{path.name}: storage answered {status}")
        self.stdout.write(self.style.SUCCESS(f"  {path.name}: uploaded"))
        return str(reply.get("key", ""))

    async def _wait_for_indexing(self, meko: MekoGateway, pending: list[tuple[str, str]]) -> None:
        for name, key in pending:
            for _ in range(STATUS_POLL_ATTEMPTS):
                status = await meko.call("knowledgebase_document_status", document_key=key)
                state = str(status.get("state", "")) if isinstance(status, Mapping) else ""
                if state.lower() in {"indexed", "ready", "completed", "done", "failed", "error"}:
                    self.stdout.write(f"  {name}: {state}")
                    break
                await anyio.sleep(STATUS_POLL_SECONDS)
            else:
                self.stdout.write(self.style.WARNING(f"  {name}: still indexing; check the Meko portal"))
