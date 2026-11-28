"""Security headers for the DejaFix pages.

Django 5.2 has no built-in Content-Security-Policy (it arrives in 6.0), so this sets a strict
one. Scripts and styles load only from our own static files (app.js, app.css): nothing inline,
no other origin. app.js only enhances the pages; they work without it. The admin keeps Django's
defaults: it relies on its own inline scripts.
"""

from __future__ import annotations

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
EXEMPT_PREFIXES = ("/admin/",)


class ContentSecurityPolicyMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if not request.path.startswith(EXEMPT_PREFIXES):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response
