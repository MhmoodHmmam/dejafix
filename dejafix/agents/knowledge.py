"""The self-learning loop: find where the agents' context was weak, fill it, and measure the fix.

Meko logs every `context_search` and grades it (high / mid / low). Its self-learning tools list
the weak searches (`selflearning_status`, `selflearning_shortfalls`, `selflearning_query_log`),
and `selflearning_verify` replays one after a fix. DejaFix closes the loop:

1. scan: import weak searches made by our resolvers (other agents' searches are ignored);
2. draft: the librarian writes an article from human-approved fixes only;
3. publish: after a human approves the draft, upload it to the knowledge base, which
   context_search reads (promoted memories it does not, see the decision log);
4. verify: replay the search and record its band after the fix.

The knowledge base is trusted context for every resolver, so nothing is uploaded without a
human. This module only talks to Meko and Claude; `support.gaps` owns the database side.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx2

from dejafix.agents import prompts
from dejafix.agents.schemas import GapArticle, clip
from dejafix.agents.trace import AgentTrace
from dejafix.llm import LLMUsage, StructuredLLM
from dejafix.meko import MekoGateway, MekoToolError, MekoUnavailable

LIBRARIAN_AGENT = "dejafix-librarian"
ADD_CONTEXT_TODO = "add_context"
MAX_SHORTFALL_PAGES = 5  # 10 searches a page
HEAD_BYTES = 3072  # Meko sniffs the first 3072 bytes against the extension
UPLOAD_TIMEOUT_SECONDS = 30.0
ARTICLE_CONTENT_TYPE = "text/markdown"
ARTICLE_SEARCH_LIMIT = 8
ARTICLE_PASSAGES = 4
MAX_TITLE_CHARS = 120
MAX_BODY_CHARS = 4000
INDEXED_STATES = frozenset({"indexed", "ready", "completed", "done"})
FAILED_STATES = frozenset({"failed", "error"})
VERIFIED_BAND = "high"

Uploader = Callable[[str, Mapping[str, str], str, bytes, str], Awaitable[int]]


# --- Uploading documents ----------------------------------------------------------------------


async def post_to_presigned_url(url: str, fields: Mapping[str, str], filename: str, content: bytes, content_type: str) -> int:
    # The URL comes from Meko, but it receives our document: only ever send it over HTTPS.
    if urlsplit(url).scheme != "https":
        raise MekoToolError("knowledgebase_upload_document", "insecure_upload_url", "upload URL is not https")
    try:
        async with httpx2.AsyncClient(timeout=UPLOAD_TIMEOUT_SECONDS) as client:
            response = await client.post(url, data=dict(fields), files={"file": (filename, content, content_type)})
    except httpx2.HTTPError as exc:
        raise MekoUnavailable(f"document upload failed: {type(exc).__name__}") from exc
    return response.status_code


@dataclass(frozen=True)
class UploadResult:
    key: str
    already_uploaded: bool


async def upload_document(
    meko: MekoGateway, *, filename: str, content: bytes, content_type: str, uploader: Uploader
) -> UploadResult:
    """Ask Meko for a presigned URL and POST the bytes there (fields first, file last)."""
    reply = await meko.call(
        "knowledgebase_upload_document",
        filename=filename,
        size=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
        content_head_base64=base64.b64encode(content[:HEAD_BYTES]).decode("ascii"),
    )
    if not isinstance(reply, Mapping):
        raise MekoToolError("knowledgebase_upload_document", "unexpected_reply", "reply is not an object")
    key = str(reply.get("key") or filename)
    if reply.get("already_uploaded"):
        return UploadResult(key=key, already_uploaded=True)
    url = reply.get("url")
    if not isinstance(url, str) or not url:
        raise MekoToolError("knowledgebase_upload_document", "unexpected_reply", "no upload URL")
    status = await uploader(url, reply.get("fields") or {}, filename, content, content_type)
    if not 200 <= status < 300:
        raise MekoToolError("knowledgebase_upload_document", "upload_failed", f"{filename}: storage answered {status}")
    return UploadResult(key=key, already_uploaded=False)


async def document_state(meko: MekoGateway, key: str) -> str:
    status = await meko.call("knowledgebase_document_status", document_key=key)
    return str(status.get("state", "")).lower() if isinstance(status, Mapping) else ""


# --- 1. Scan ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class GapCandidate:
    log_id: str
    query: str
    agent_id: str
    conversation_id: str
    band: str
    best_score: float | None
    reason: str
    count: int
    state: str


@dataclass
class GapScan:
    quality_pct: int | None = None
    candidates: list[GapCandidate] | None = None
    ignored: int = 0


def normalize_conversation_id(value: Any) -> str:
    """conversation_create returns 32 hex digits; the search log writes the same id as a UUID."""
    return str(value or "").replace("-", "").lower()


def _score_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return min(max(float(value), 0.0), 1.0)
    return None


async def scan_gaps(meko: MekoGateway, *, our_agents: Collection[str]) -> GapScan:
    """Weak searches behind Meko's `add_context` to-do that our own agents made."""
    status = await meko.call("selflearning_status")
    scan = GapScan(candidates=[])
    if not isinstance(status, Mapping):
        return scan
    quality = status.get("quality")
    if isinstance(quality, Mapping) and isinstance(quality.get("pct"), int):
        scan.quality_pct = quality["pct"]
    if ADD_CONTEXT_TODO not in (status.get("open_fixes") or []):
        return scan

    for page in range(1, MAX_SHORTFALL_PAGES + 1):
        reply = await meko.call("selflearning_shortfalls", todo_id=ADD_CONTEXT_TODO, page=page)
        rows = reply.get("searches") if isinstance(reply, Mapping) else None
        for row in rows if isinstance(rows, list) else []:
            candidate = await _candidate(meko, row, our_agents)
            if candidate is None:
                scan.ignored += 1
            else:
                scan.candidates.append(candidate)
        if not (isinstance(reply, Mapping) and reply.get("has_more")):
            break
    return scan


async def _candidate(meko: MekoGateway, row: Any, our_agents: Collection[str]) -> GapCandidate | None:
    if not isinstance(row, Mapping) or row.get("state") == "verified":
        return None
    log_id, query = row.get("log_id"), row.get("query")
    if not isinstance(log_id, str) or not log_id or not isinstance(query, str) or not query.strip():
        return None
    # Shortfall rows don't say who searched; the log entry does (and links the conversation).
    detail = await meko.call("selflearning_query_log", log_id=log_id)
    agent_id = detail.get("agent_id") if isinstance(detail, Mapping) else None
    if agent_id not in our_agents:
        return None
    count = row.get("count")
    return GapCandidate(
        log_id=log_id,
        query=clip(query, 500),
        agent_id=str(agent_id),
        conversation_id=normalize_conversation_id(detail.get("conversation_id")),
        band=str(row.get("band") or "low")[:10],
        best_score=_score_or_none(row.get("best_score")),
        reason=str(row.get("reason") or "")[:40],
        count=count if isinstance(count, int) and count > 0 else 1,
        state=str(row.get("state") or ""),
    )


# --- 2. Draft ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class DraftResult:
    covers_query: bool
    title: str
    body: str
    reasoning: str
    usage: LLMUsage


async def draft_article(
    meko: MekoGateway, llm: StructuredLLM, *, gap_ref: str, query: str, approved_fixes: Sequence[str]
) -> DraftResult:
    """The librarian drafts an article for a weak search, from human-approved fixes and the docs."""
    trace = AgentTrace(meko, agent=LIBRARIAN_AGENT, ticket_ref=gap_ref)
    librarian = await trace.open()
    found = await librarian.call("knowledgebase_search", query=query, limit=ARTICLE_SEARCH_LIMIT)
    hits = found.get("results") if isinstance(found, Mapping) else None
    passages = [
        str(hit.get("chunk_text", "")).strip()
        for hit in (hits if isinstance(hits, list) else [])
        # Promoted memories are excluded: approved fixes come only from our own approved records.
        if isinstance(hit, Mapping) and (hit.get("metadata_filters") or {}).get("source") != "memory"
    ]
    passages = [passage for passage in passages if passage][:ARTICLE_PASSAGES]

    drafted = await llm.run(
        system=prompts.LIBRARIAN_SYSTEM,
        prompt=prompts.gap_article_prompt(query, approved_fixes, passages),
        schema=GapArticle,
        effort="low",
    )
    article = drafted.output
    covers = article.covers_query and bool(approved_fixes) and bool(article.body.strip())
    title = clip(article.title, MAX_TITLE_CHARS)
    body = article.body.strip()[:MAX_BODY_CHARS]
    await trace.record(
        "draft_article",
        input_text=query,
        output={"covers_query": covers, "title": title, "cited_sources": article.cited_sources[:6]},
        reasoning=article.reasoning,
        plan=["Read the weak search and human-approved fixes", "Draft an article for human review"],
        usage=drafted.usage,
    )
    return DraftResult(covers_query=covers, title=title, body=body, reasoning=clip(article.reasoning, 600), usage=drafted.usage)


def compose_article(*, title: str, body: str, source_ticket: str) -> bytes:
    """The published document: the reviewed draft plus where its knowledge came from."""
    source = (
        f"Source: the human-approved DejaFix fix for ticket {source_ticket}, reviewed again before publishing."
        if source_ticket
        else "Source: written by a LumenPay support reviewer."
    )
    return f"# {title.strip()}\n\n{body.strip()}\n\n---\n{source}\n".encode("utf-8")


# --- 3. Publish -------------------------------------------------------------------------------


async def publish_article(
    meko: MekoGateway, *, gap_ref: str, filename: str, content: bytes, uploader: Uploader
) -> UploadResult:
    trace = AgentTrace(meko, agent=LIBRARIAN_AGENT, ticket_ref=gap_ref)
    librarian = await trace.open()
    uploaded = await upload_document(librarian, filename=filename, content=content, content_type=ARTICLE_CONTENT_TYPE, uploader=uploader)
    await trace.record(
        "publish_article",
        input_text=filename,
        output={"document_key": uploaded.key, "already_uploaded": uploaded.already_uploaded},
        reasoning="A human approved this article, so it joins the knowledge base every resolver searches.",
    )
    return uploaded


# --- 4. Verify --------------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyResult:
    band_after: str
    score_after: float | None
    test_prompt_id: str
    indexing: bool
    top_source: str = ""  # what the replay ranked first: knowledge_base, memory or conversation

    @property
    def verified(self) -> bool:
        return self.band_after == VERIFIED_BAND


async def article_rank(meko: MekoGateway, *, query: str, title: str) -> int | None:
    """1-based rank of our article among the knowledge-base results for the query, or None.

    Meko chunks a markdown document under its headings ("Section: <title> > ..."), so the
    article is recognised by its title at the start of a chunk. Promoted memories are skipped.
    """
    if not title.strip():
        return None
    found = await meko.call("knowledgebase_search", query=query, limit=ARTICLE_SEARCH_LIMIT)
    hits = found.get("results") if isinstance(found, Mapping) else None
    documents = [
        str(hit.get("chunk_text", ""))
        for hit in (hits if isinstance(hits, list) else [])
        if isinstance(hit, Mapping) and (hit.get("metadata_filters") or {}).get("source") != "memory"
    ]
    for rank, text in enumerate(documents, start=1):
        if title.strip() in text[: len(title) + 40]:
            return rank
    return None


async def verify_gap(meko: MekoGateway, *, log_id: str, test_prompt_id: str) -> VerifyResult:
    """Replay a weak search (Meko saves it as a test prompt the first time)."""
    if test_prompt_id:
        reply = await meko.call("selflearning_verify", test_prompt_id=test_prompt_id)
    else:
        reply = await meko.call("selflearning_verify", log_id=log_id)
    return parse_verify_reply(reply, fallback_test_prompt_id=test_prompt_id)


def parse_verify_reply(reply: Any, *, fallback_test_prompt_id: str = "") -> VerifyResult:
    if not isinstance(reply, Mapping):
        raise MekoToolError("selflearning_verify", "unexpected_reply", "reply is not an object")
    after = reply.get("after")
    if not isinstance(after, Mapping):
        after = reply.get("latest_run") if isinstance(reply.get("latest_run"), Mapping) else {}
    band = after.get("band") or reply.get("band_after")
    if not isinstance(band, str) or not band:
        raise MekoToolError("selflearning_verify", "unexpected_reply", "no band after the replay")
    top = reply.get("top_results")
    first = top[0] if isinstance(top, list) and top and isinstance(top[0], Mapping) else {}
    return VerifyResult(
        band_after=band[:10],
        score_after=_score_or_none(after.get("best_score", reply.get("best_score_after"))),
        test_prompt_id=str(reply.get("test_prompt_id") or fallback_test_prompt_id),
        indexing=bool(reply.get("indexing")),
        top_source=str(first.get("s") or "")[:20],
    )
