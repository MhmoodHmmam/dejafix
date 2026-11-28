"""Login with a failure limit, since Django has no built-in login throttling.

After MAX_FAILURES failed attempts for one username from one address within WINDOW_SECONDS, the
login answers 429 for that pair until the window passes, even with the right password. The same
message is shown whether or not the user exists. The address is REMOTE_ADDR: X-Forwarded-For is
not trusted, because any client can set it. The counter lives in the cache (per process with the
default backend).
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.contrib.auth import views as auth_views
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse

MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
TOO_MANY_REQUESTS = 429
THROTTLED_MESSAGE = "Too many failed attempts. Try again in 15 minutes."


def _key(request: HttpRequest) -> str:
    username = str(request.POST.get("username", "")).strip().lower()
    address = request.META.get("REMOTE_ADDR", "")
    digest = hashlib.sha256(f"{address}|{username}".encode()).hexdigest()  # no raw usernames in cache keys
    return f"dejafix:login-failures:{digest}"


class ThrottledLoginView(auth_views.LoginView):
    redirect_authenticated_user = True

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if cache.get(_key(request), 0) >= MAX_FAILURES:
            context = self.get_context_data(form=self.get_form(), throttled_message=THROTTLED_MESSAGE)
            return self.render_to_response(context, status=TOO_MANY_REQUESTS)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = _key(self.request)
        if not cache.add(key, 1, WINDOW_SECONDS):
            try:
                cache.incr(key)
            except ValueError:  # expired between add and incr
                cache.set(key, 1, WINDOW_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(_key(self.request))
        return super().form_valid(form)
