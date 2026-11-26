"""DATABASES['default'] from the environment.

Without DEJAFIX_DB_HOST, DejaFix uses a local SQLite file (tests, quick starts, judges' clones).
With it, Django connects to Postgres, such as a cluster created in Meko with `amp_cluster_create`.
A half-configured Postgres fails at startup instead of silently falling back to SQLite.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from config.env import TRUTHY_VALUES

DEFAULT_PORT = 5432
MAX_PORT = 65535
CONNECT_TIMEOUT_SECONDS = 10
# Keep connections for a minute: each new one costs a TLS handshake to a remote database.
CONN_MAX_AGE_SECONDS = 60
# Encrypted modes only; "prefer" and weaker can fall back to plaintext.
ALLOWED_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})
REQUIRED_POSTGRES_VARS = ("DEJAFIX_DB_NAME", "DEJAFIX_DB_USER", "DEJAFIX_DB_PASSWORD")


def _get(environ: Mapping[str, str], name: str, default: str = "") -> str:
    return environ.get(name, default).strip()


def _port(raw: str) -> int:
    if not raw:
        return DEFAULT_PORT
    if not raw.isdigit() or not 0 < int(raw) <= MAX_PORT:
        raise ImproperlyConfigured("DEJAFIX_DB_PORT must be a number from 1 to 65535.")
    return int(raw)


def database_config(environ: Mapping[str, str], base_dir: Path, *, running_tests: bool = False) -> dict[str, Any]:
    """`running_tests`: the suite stays offline and fast on SQLite unless DEJAFIX_TEST_ON_POSTGRES opts in."""
    host = _get(environ, "DEJAFIX_DB_HOST")
    use_postgres_for_tests = _get(environ, "DEJAFIX_TEST_ON_POSTGRES").lower() in TRUTHY_VALUES
    if not host or (running_tests and not use_postgres_for_tests):
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": base_dir / "db.sqlite3"}

    missing = [name for name in REQUIRED_POSTGRES_VARS if not _get(environ, name)]
    if missing:
        raise ImproperlyConfigured(f"DEJAFIX_DB_HOST is set, so these are required too: {', '.join(missing)}.")
    sslmode = _get(environ, "DEJAFIX_DB_SSLMODE", "require")
    if sslmode not in ALLOWED_SSLMODES:
        raise ImproperlyConfigured(f"DEJAFIX_DB_SSLMODE must be one of {', '.join(sorted(ALLOWED_SSLMODES))}.")

    return {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": host,
        "PORT": _port(_get(environ, "DEJAFIX_DB_PORT")),
        "NAME": _get(environ, "DEJAFIX_DB_NAME"),
        "USER": _get(environ, "DEJAFIX_DB_USER"),
        "PASSWORD": environ["DEJAFIX_DB_PASSWORD"].strip(),
        "CONN_MAX_AGE": CONN_MAX_AGE_SECONDS,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"sslmode": sslmode, "connect_timeout": CONNECT_TIMEOUT_SECONDS},
    }
