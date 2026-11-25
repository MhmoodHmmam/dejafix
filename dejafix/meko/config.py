"""Validated Meko connection settings."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from dejafix.meko.errors import MekoConfigError

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
MIN_TIMEOUT_SECONDS = 0.05
MAX_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class MekoConfig:
    mcp_url: str
    # repr=False keeps the key out of logs, tracebacks and debug pages.
    api_key: str = field(repr=False)
    timeout_seconds: float = 30.0
    # Every DejaFix conversation, memory and document lives in this one datapack.
    datapack_id: str = ""

    def __post_init__(self) -> None:
        if not self.api_key:
            raise MekoConfigError("MEKO_API_KEY is not set. Add it to .env (see .env.example).")
        if self.datapack_id:
            try:
                uuid.UUID(self.datapack_id)
            except ValueError as exc:
                raise MekoConfigError("MEKO_DATAPACK_ID must be the datapack's UUID.") from exc
        parts = urlsplit(self.mcp_url)
        is_local = parts.hostname in LOCAL_HOSTS
        # Plain HTTP would send the bearer key in clear text; only allow it for a local Meko.
        if parts.scheme != "https" and not (parts.scheme == "http" and is_local):
            raise MekoConfigError("MEKO_MCP_URL must use https (http is allowed only for localhost).")
        if not parts.hostname:
            raise MekoConfigError("MEKO_MCP_URL has no host.")
        if not MIN_TIMEOUT_SECONDS <= self.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise MekoConfigError(
                f"MEKO_TIMEOUT_SECONDS must be between {MIN_TIMEOUT_SECONDS} and {MAX_TIMEOUT_SECONDS}."
            )

    @classmethod
    def from_settings(cls) -> MekoConfig:
        from django.conf import settings

        return cls(
            mcp_url=settings.MEKO_MCP_URL,
            api_key=settings.MEKO_API_KEY,
            timeout_seconds=settings.MEKO_TIMEOUT_SECONDS,
            datapack_id=settings.MEKO_DATAPACK_ID,
        )
