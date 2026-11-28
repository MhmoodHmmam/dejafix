"""Live progress of a background agent run, kept in the cache for the ticket page to poll.

Only step names and numbers are stored (no ticket text). The cache is per process with the default
backend, which fits the single-process demo server; several workers need a shared cache.
"""

from __future__ import annotations

import time
from typing import Any

from django.core.cache import cache

PROGRESS_TTL_SECONDS = 15 * 60
KEY = "dejafix:progress:{}"


def start(ticket_pk: int) -> None:
    cache.set(KEY.format(ticket_pk), {"started": time.time(), "steps": []}, PROGRESS_TTL_SECONDS)


def add_step(ticket_pk: int, *, step: str, label: str, agent: str, tokens: int, ms: int, meko_calls: int) -> None:
    data = read(ticket_pk) or {"started": time.time(), "steps": []}
    data["steps"] = [*data["steps"], {"step": step, "label": label, "agent": agent, "tokens": tokens, "ms": ms,
                                      "meko_calls": meko_calls}]
    cache.set(KEY.format(ticket_pk), data, PROGRESS_TTL_SECONDS)


def read(ticket_pk: int) -> dict[str, Any] | None:
    return cache.get(KEY.format(ticket_pk))
