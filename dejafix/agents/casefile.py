"""Decision records stored whole in Meko as artifacts (`artifact_put`).

A case file is written when a human approves or rejects a fix. It holds what the agents decided
and why (steps, reasoning, retrieval scores, tokens) and links back from the shared memory
("Case file: <sha256>"), so any agent can fetch the evidence behind a collective fix.

It never holds the ticket text, the customer or the reviewer's name. The JSON is built without
timestamps and with sorted keys, so a retry produces the same bytes, and Meko's artifact_put
(idempotent by content) stores it once.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from dejafix.meko import MekoGateway, MekoToolError

CASE_FILE_VERSION = 1
JSON_CONTENT_TYPE = "application/json"
# Meko stores artifacts under 1 MB inline; ours are a few KB, so anything near that is a bug.
MAX_ARTIFACT_BYTES = 512_000
CASE_FILE_STEP_FIELDS = ("step", "agent", "meko_conversation_id", "decision", "reasoning", "model", "input_tokens", "output_tokens", "meko_calls")


def case_file_name(ticket_ref: str) -> str:
    return f"dejafix-case-{ticket_ref}.json"


def build_case_file(
    *,
    ticket_ref: str,
    decision: str,
    fix: Mapping[str, Any],
    steps: Sequence[Mapping[str, Any]],
    human_note: str,
    extra: Mapping[str, Any] | None = None,
) -> bytes:
    document = {
        "app": "dejafix",
        "kind": "case_file",
        "version": CASE_FILE_VERSION,
        "ticket": ticket_ref,
        "human_decision": decision,
        "decided_by": "human reviewer",
        "human_note": human_note,
        "fix": dict(fix),
        "agent_steps": [{key: step.get(key) for key in CASE_FILE_STEP_FIELDS} for step in steps],
        **(extra or {}),
    }
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2, default=str).encode("utf-8")


async def put_artifact(meko: MekoGateway, *, filename: str, content: bytes, content_type: str) -> str:
    """Store bytes as a Meko artifact and return their SHA-256 (Meko's artifact id)."""
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError(f"artifact {filename} is {len(content)} bytes; limit is {MAX_ARTIFACT_BYTES}")
    expected = hashlib.sha256(content).hexdigest()
    reply = await meko.call(
        "artifact_put",
        filename=filename,
        content_base64=base64.b64encode(content).decode("ascii"),
        content_type=content_type,
    )
    returned = reply.get("content_hash") if isinstance(reply, Mapping) else None
    if returned and returned != expected:
        raise MekoToolError("artifact_put", "hash_mismatch", "Meko stored different bytes than we sent")
    return expected
