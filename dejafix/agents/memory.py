"""The text format of approved fixes and human corrections stored in Meko.

Memory text is built only from structured, length-limited fields (signature, fix, steps)
that a human approved. Raw ticket text is never written to memory.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from dejafix.agents.schemas import clip

FIX_HEADER = "DejaFix approved fix"
CORRECTION_HEADER = "DejaFix human correction"
MAX_SIGNATURE_CHARS = 200
MAX_SUMMARY_CHARS = 300
MAX_STEP_CHARS = 240
MAX_STEPS = 6
MAX_NOTE_CHARS = 600

_STEP_LINE = re.compile(r"^\d+\.\s+(.*)$")


@dataclass(frozen=True)
class ParsedFix:
    signature: str
    category: str
    summary: str
    steps: tuple[str, ...]
    case_file: str = ""


def _case_file_line(case_file: str) -> list[str]:
    # The SHA-256 of the decision record in Meko's artifact store (artifact_get fetches it).
    return [f"Case file: {case_file}"] if case_file else []


def format_fix_memory(
    *, signature: str, category: str, summary: str, steps: Sequence[str], ticket_ref: str, case_file: str = ""
) -> str:
    lines = [
        FIX_HEADER,
        f"Problem: {clip(signature, MAX_SIGNATURE_CHARS)}",
        f"Category: {category}",
        f"Fix: {clip(summary, MAX_SUMMARY_CHARS)}",
        "Steps:",
        *(f"{index}. {clip(step, MAX_STEP_CHARS)}" for index, step in enumerate(steps[:MAX_STEPS], start=1)),
        f"Source ticket: {ticket_ref}",
        *_case_file_line(case_file),
    ]
    return "\n".join(lines)


def parse_fix_memory(text: str) -> ParsedFix | None:
    """Read back a fix written by `format_fix_memory`; None for any other memory."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != FIX_HEADER:
        return None
    fields: dict[str, str] = {}
    steps: list[str] = []
    for line in lines[1:]:
        step = _STEP_LINE.match(line)
        if step:
            steps.append(step.group(1))
            continue
        key, separator, value = line.partition(": ")
        if separator:
            fields[key] = value
    if "Fix" not in fields or "Problem" not in fields:
        return None
    return ParsedFix(
        signature=fields["Problem"],
        category=fields.get("Category", "other"),
        summary=fields["Fix"],
        steps=tuple(steps),
        case_file=fields.get("Case file", ""),
    )


def is_correction_memory(text: str) -> bool:
    """True only for text written by `format_correction_memory` (exact header line)."""
    lines = text.strip().splitlines()
    return bool(lines) and lines[0].strip() == CORRECTION_HEADER and any(
        line.startswith("Reviewer note: ") for line in lines
    )


def format_correction_memory(*, signature: str, rejected_summary: str, note: str, case_file: str = "") -> str:
    return "\n".join(
        [
            CORRECTION_HEADER,
            f"Problem: {clip(signature, MAX_SIGNATURE_CHARS)}",
            f"Rejected fix: {clip(rejected_summary, MAX_SUMMARY_CHARS)}",
            f"Reviewer note: {clip(note, MAX_NOTE_CHARS)}",
            *_case_file_line(case_file),
        ]
    )
