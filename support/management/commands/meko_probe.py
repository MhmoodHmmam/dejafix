"""Connect to Meko, list its tools and save their live input schemas.

Meko's docs do not publish JSON input schemas, so the agents are built against what the
server itself reports. Usage: `python manage.py meko_probe [--smoke] [--output PATH]`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from dejafix.meko import MekoConfig, MekoError, MekoGateway, MekoSession, MekoToolError

DEFAULT_OUTPUT = Path("docs") / "meko-tools.json"
SMOKE_TOOL = "datapack_list"
PROBE_AGENT_ID = "dejafix-probe"
# A fixed session_id is Meko's dedup key, so repeated probes reuse one conversation.
PROBE_SESSION_ID = "dejafix-probe"


class Command(BaseCommand):
    help = "List Meko's MCP tools and save their input schemas (read-only)."

    # Tests swap this for a session bound to an in-process fake server.
    session_factory = MekoSession

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Where to write the schemas.")
        parser.add_argument(
            "--smoke",
            action="store_true",
            help=f"Also open (or reuse) a probe conversation and call the read-only `{SMOKE_TOOL}` in it.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        self._smoke_error: MekoError | None = None
        try:
            config = MekoConfig.from_settings()
            if options["smoke"] and not config.datapack_id:
                raise CommandError(
                    "--smoke needs MEKO_DATAPACK_ID: create a datapack in the Meko Cloud portal and put its "
                    "UUID in .env (Meko cannot open a conversation without a datapack)."
                )
            tools, smoke_reply = anyio.run(self._probe, config, options["smoke"])
        except MekoError as exc:
            raise CommandError(f"Meko probe failed: {exc}") from exc

        output: Path = options["output"]
        if not output.is_absolute():
            output = Path(settings.BASE_DIR) / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(tools, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        for tool in tools:
            required = ", ".join(tool["input_schema"].get("required") or []) or "-"
            self.stdout.write(f"  {tool['name']:<32} required: {required}")
        self.stdout.write(self.style.SUCCESS(f"{len(tools)} tools saved to {output}"))
        if self._smoke_error is not None:
            raise CommandError(f"Schemas saved, but the smoke call failed: {self._smoke_error}")
        if options["smoke"]:
            self.stdout.write(self.style.SUCCESS(f"{SMOKE_TOOL} answered: {self._summarize(smoke_reply)}"))

    async def _probe(self, config: MekoConfig, smoke: bool) -> tuple[list[dict[str, Any]], Any]:
        async with self.session_factory(config) as meko:
            tools = [
                {"name": tool.name, "description": tool.description or "", "input_schema": tool.input_schema}
                for tool in sorted(meko.tools.values(), key=lambda tool: tool.name)
            ]
            if not smoke:
                return tools, None
            try:
                return tools, await self._smoke(meko)
            except MekoError as exc:
                # Keep the schemas: they are what we need to fix the failing call.
                self._smoke_error = exc
                return tools, None

    @staticmethod
    async def _smoke(meko: MekoGateway) -> Any:
        probe = meko.as_agent(PROBE_AGENT_ID)
        conversation = await probe.call("conversation_create", title="DejaFix probe", session_id=PROBE_SESSION_ID)
        conversation_id = conversation.get("id") if isinstance(conversation, dict) else None
        if not isinstance(conversation_id, str):
            raise MekoToolError("conversation_create", "unexpected_reply", "no conversation id in the reply")
        return await probe.in_conversation(conversation_id).call(SMOKE_TOOL)

    @staticmethod
    def _summarize(reply: Any) -> str:
        # Print shape, not content: datapack names may be private.
        if isinstance(reply, dict):
            return "object with keys " + ", ".join(sorted(reply))
        if isinstance(reply, list):
            return f"list of {len(reply)} item(s)"
        return type(reply).__name__
