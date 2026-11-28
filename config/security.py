"""HTTPS hardening for a real deployment, opt-in with DJANGO_HTTPS=1.

The demo runs locally over http://127.0.0.1, where secure-only cookies would make logging in
impossible, so these stay off by default. Turned on, they clear the four HTTPS warnings of
`manage.py check --deploy` (W004, W008, W012, W016): secure session and CSRF cookies, an HTTPS
redirect, and HSTS. HSTS starts short (one hour) because browsers remember it; raise
DJANGO_HSTS_SECONDS once the site is known to work over HTTPS only. HSTS for subdomains and
preload (W005, W021) are left to whoever owns the domain: both are hard to undo.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured

from config.env import TRUTHY_VALUES

DEFAULT_HSTS_SECONDS = 3600


def https_settings(environ: Mapping[str, str]) -> dict[str, Any]:
    if environ.get("DJANGO_HTTPS", "").strip().lower() not in TRUTHY_VALUES:
        return {}
    raw = environ.get("DJANGO_HSTS_SECONDS", "").strip() or str(DEFAULT_HSTS_SECONDS)
    if not raw.isdigit() or int(raw) <= 0:
        raise ImproperlyConfigured("DJANGO_HSTS_SECONDS must be a positive whole number of seconds.")
    return {
        "SECURE_SSL_REDIRECT": True,
        "SESSION_COOKIE_SECURE": True,
        "CSRF_COOKIE_SECURE": True,
        "SECURE_HSTS_SECONDS": int(raw),
    }
