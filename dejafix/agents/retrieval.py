"""What the resolver knows before it solves anything: one pass over Meko's shared context.

Two calls, because they cover different things (verified against Meko Cloud, see the decision log):
- `context_search`: private memories (human corrections), past conversations, knowledge base.
- `knowledgebase_search`: documentation chunks AND promoted (collective) fixes. Promoted fixes
  do not come back from `context_search`.

A promoted fix is trusted only if our curator agent wrote it: anything else in the datapack
(another tool, a test, a poisoning attempt) is ignored as a fix.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from dejafix.agents.memory import is_correction_memory, parse_fix_memory
from dejafix.meko import MekoGateway

CONTEXT_SEARCH_LIMIT = 5
KB_SEARCH_LIMIT = 8
MAX_PASSAGES = 5
MAX_LEARNINGS = 3
# Meko's rerank score for a promoted fix. Above this, the resolver checks whether the fix applies
# (a cheap low-effort call) before solving from scratch. Calibrated in the week 4 benchmark.
REUSE_MIN_RERANK_SCORE = 0.80
# context_search keeps weak memory rows on the "auto" path, so corrections need their own floor.
LEARNING_MIN_SCORE = 0.30
PROMOTED_SOURCE = "memory"


@dataclass(frozen=True)
class ApprovedFix:
    memory_id: str
    text: str
    score: float


@dataclass
class Retrieval:
    approved_fixes: list[ApprovedFix] = field(default_factory=list)
    passages: list[str] = field(default_factory=list)
    learnings: list[str] = field(default_factory=list)
    match_quality: str = "unknown"
    tokens_saved: int = 0

    @property
    def best_fix(self) -> ApprovedFix | None:
        candidates = [fix for fix in self.approved_fixes if fix.score >= REUSE_MIN_RERANK_SCORE]
        return max(candidates, key=lambda fix: fix.score, default=None)

    def summary(self) -> dict[str, Any]:
        best = self.best_fix
        return {
            "approved_fixes": [{"memory_id": fix.memory_id, "score": round(fix.score, 3)} for fix in self.approved_fixes],
            "reuse_candidate": best.memory_id if best else None,
            "passages": len(self.passages),
            "learnings": len(self.learnings),
            "match_quality": self.match_quality,
            "tokens_saved": self.tokens_saved,
        }


def _text_of(hit: Mapping[str, Any]) -> str:
    for key in ("chunk_text", "memory", "text", "content"):
        value = hit.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _dicts(value: Any) -> Iterable[Mapping[str, Any]]:
    return (item for item in value if isinstance(item, Mapping)) if isinstance(value, list) else ()


def _score(hit: Mapping[str, Any], key: str) -> float:
    value = hit.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


async def retrieve(meko: MekoGateway, *, signature: str, subject: str, trusted_curator: str) -> Retrieval:
    """Search Meko before solving. `meko` must be scoped to the resolver's conversation."""
    retrieval = Retrieval()

    context = await meko.call("context_search", query=signature, prompt=subject, limit=CONTEXT_SEARCH_LIMIT)
    if isinstance(context, Mapping):
        results = context.get("results") if isinstance(context.get("results"), Mapping) else {}
        retrieval.match_quality = str(context.get("match_quality", "unknown"))
        savings = context.get("token_savings")
        if isinstance(savings, Mapping):
            retrieval.tokens_saved = int(_score(savings, "tokens_saved"))
        for hit in _dicts(results.get("memory")):
            text = _text_of(hit)
            # Exact format only: Meko also auto-extracts lookalike memories from conversation turns.
            if is_correction_memory(text) and _score(hit, "score") >= LEARNING_MIN_SCORE:
                retrieval.learnings.append(text)
        for hit in _dicts(results.get("knowledge_base")):
            if text := _text_of(hit):
                retrieval.passages.append(text)

    knowledge = await meko.call("knowledgebase_search", query=signature, limit=KB_SEARCH_LIMIT)
    hits = knowledge.get("results") if isinstance(knowledge, Mapping) else None
    for hit in _dicts(hits):
        text = _text_of(hit)
        filters = hit.get("metadata_filters") if isinstance(hit.get("metadata_filters"), Mapping) else {}
        if filters.get("source") == PROMOTED_SOURCE:
            if filters.get("agent_id") == trusted_curator and parse_fix_memory(text) is not None:
                memory_id = str(filters.get("memory_id") or hit.get("id") or "")
                if memory_id:
                    retrieval.approved_fixes.append(ApprovedFix(memory_id, text, _score(hit, "rerank_score")))
        elif text and text not in retrieval.passages:
            retrieval.passages.append(text)

    retrieval.passages = retrieval.passages[:MAX_PASSAGES]
    retrieval.learnings = retrieval.learnings[:MAX_LEARNINGS]
    return retrieval
