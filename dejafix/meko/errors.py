"""Errors raised by the Meko gateway. Messages never include credentials or call arguments."""

from __future__ import annotations


class MekoError(Exception):
    """Base class for every Meko gateway failure."""


class MekoConfigError(MekoError):
    """Meko is not configured (missing key, invalid URL)."""


class MekoUnavailable(MekoError):
    """Meko could not be reached or did not answer (network, 5xx). Maps to HTTP 503."""


class MekoTimeout(MekoUnavailable):
    """A Meko call took longer than the configured timeout."""


class MekoAuthError(MekoError):
    """Meko rejected the credentials (HTTP 401/403). Never retried."""


class MekoArgumentError(MekoError):
    """Arguments do not match the tool's published input schema; nothing was sent."""


class MekoToolError(MekoError):
    """Meko answered with an error: an in-band `{"error": ...}` reply or an MCP `isError` result."""

    def __init__(self, tool: str, code: str, detail: str = "") -> None:
        self.tool = tool
        self.code = code
        self.detail = detail
        super().__init__(f"{tool}: {code}" + (f" ({detail})" if detail else ""))
