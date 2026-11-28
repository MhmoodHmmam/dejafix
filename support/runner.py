"""Run the agents outside the request, with live progress.

The view claims the ticket (RUNNING) and returns at once; a small thread pool runs the agents
after the claim commits, and the ticket page polls `support.progress`. Tests and management
commands run inline (DEJAFIX_RUN_AGENTS_INLINE), so they stay synchronous and deterministic.

This suits the single-process demo server. A deployment with several processes would move the
pool to a worker process; the claim already makes a second start impossible.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import close_old_connections, transaction

from dejafix.llm import LLMError, StructuredLLM
from support.models import Ticket
from support.services import SessionFactory, claim_ticket, resolve_ticket

logger = logging.getLogger("dejafix.support")

MAX_WORKERS = 2
LLMFactory = Callable[[], StructuredLLM]

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="dejafix-agents")

claim = claim_ticket


def _release(ticket_pk: int, reason: str) -> None:
    Ticket.objects.filter(pk=ticket_pk, status=Ticket.Status.RUNNING).update(
        status=Ticket.Status.FAILED, failure_reason=reason[:300]
    )


def run_job(ticket_pk: int, *, llm_factory: LLMFactory, session_factory: SessionFactory | None = None) -> None:
    """Run the agents on a ticket this process has claimed. Never leaves it RUNNING on an error."""
    ticket = Ticket.objects.get(pk=ticket_pk)
    try:
        llm = llm_factory()
    except LLMError as exc:
        logger.warning("Ticket %s: Claude is not available (%s).", ticket.external_id, type(exc).__name__)
        _release(ticket_pk, f"Claude is not available: {exc}")
        return
    resolve_ticket(ticket, llm=llm, session_factory=session_factory, already_claimed=True)


def background_job(ticket_pk: int, llm_factory: LLMFactory) -> None:
    """The thread entry point: fail closed, and give the thread's database connection back."""
    try:
        run_job(ticket_pk, llm_factory=llm_factory)
    except Exception:  # the thread boundary: anything unexpected is logged and releases the claim
        logger.exception("Ticket %s: the background run crashed.", ticket_pk)
        _release(ticket_pk, "The run stopped unexpectedly; run the agents again.")
    finally:
        close_old_connections()


def submit(ticket_pk: int, llm_factory: LLMFactory) -> None:
    _executor.submit(background_job, ticket_pk, llm_factory)


def start(ticket: Ticket, *, llm_factory: LLMFactory) -> bool:
    """Run a claimed ticket: inline (tests, commands) or in the pool once the claim has committed.

    Returns True if the run already finished (inline)."""
    if settings.DEJAFIX_RUN_AGENTS_INLINE:
        run_job(ticket.pk, llm_factory=llm_factory)
        return True
    transaction.on_commit(lambda: submit(ticket.pk, llm_factory))
    return False
