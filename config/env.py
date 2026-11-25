"""Minimal `.env` loader and typed environment readers (standard library only)."""

from __future__ import annotations

import os
from pathlib import Path

TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines. Blank lines and `#` comments are ignored; matching quotes are stripped."""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_dotenv(path: Path) -> None:
    """Load `path` into os.environ without overriding variables that are already set."""
    if not path.is_file():
        return
    # utf-8-sig tolerates the BOM that some Windows editors add.
    for key, value in parse_dotenv(path.read_text(encoding="utf-8-sig")).items():
        os.environ.setdefault(key, value)


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUTHY_VALUES


def env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)
