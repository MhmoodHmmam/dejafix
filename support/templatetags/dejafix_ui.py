"""Display helpers for the staff pages: agent decisions as readable HTML, short Meko IDs.

Agent decisions are JSON written by the pipeline and partly by the model, so every value is
escaped: HTML is built only with format_html / format_html_join.
"""

from __future__ import annotations

import re
from typing import Any

from django import template
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

register = template.Library()

SHORT_ID_LENGTH = 8
# Meko memory and conversation IDs: UUIDs (hyphenated) or 32+ hex digits.
MEKO_ID = re.compile(r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32,64})$")


@register.filter
def short_id(value: str) -> str:
    """"9adc8cce-d535-…" -> "9adc8cce…"; short values are returned unchanged."""
    value = str(value or "")
    return value[:SHORT_ID_LENGTH] + "…" if len(value) > SHORT_ID_LENGTH + 2 else value


def _id_html(value: str) -> SafeString:
    return format_html('<code class="id" title="{}">{}</code>', value, short_id(value))


def _number(value: float | int) -> str:
    if isinstance(value, int):
        return f"{value:,}"
    return f"{value:.2f}" if abs(value) < 10 else f"{value:,.0f}"


def _scored_fix(item: dict[str, Any]) -> SafeString:
    return format_html(
        '<span class="chip"><span class="chip-score">{}</span>{}</span>',
        _number(float(item.get("score", 0))),
        _id_html(str(item.get("memory_id", ""))),
    )


@register.filter
def decision_value(value: Any) -> SafeString | str:
    """Render one value of an agent's decision for people, never as a Python repr."""
    if value is None or value == "":
        return format_html('<span class="muted">{}</span>', "—")
    if isinstance(value, bool):
        return format_html('<span class="flag flag-{}">{}</span>', "yes" if value else "no", "yes" if value else "no")
    if isinstance(value, (int, float)):
        return format_html('<span class="num">{}</span>', _number(value))
    if isinstance(value, str):
        return _id_html(value) if MEKO_ID.match(value) else format_html("{}", value)
    if isinstance(value, dict):
        return format_html_join(" ", '<span class="chip"><span class="chip-key">{}</span> {}</span>',
                                ((key, decision_value(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        if not value:
            return format_html('<span class="muted">{}</span>', "none")
        if all(isinstance(item, dict) and "memory_id" in item for item in value):
            return format_html('<span class="chips">{}</span>', format_html_join(" ", "{}", ((_scored_fix(item),) for item in value)))
        return format_html('<ul class="dv-list">{}</ul>', format_html_join("", "<li>{}</li>", ((decision_value(item),) for item in value)))
    return format_html("{}", value)


@register.filter
def pct_of(value: float | int, total: float | int) -> float:
    """Width (0-100) of `value` against `total`, for SVG bar attributes."""
    try:
        return round(max(0.0, min(100.0, float(value) / float(total) * 100)), 1) if total else 0.0
    except (TypeError, ValueError):
        return 0.0


@register.filter
def initials(name: str) -> str:
    words = [word for word in re.split(r"[\s\-_.&]+", str(name or "")) if word]
    return "".join(word[0] for word in words[:2]).upper() or "?"
