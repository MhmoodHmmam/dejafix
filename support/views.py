"""Staff pages: dashboard, ticket review, knowledge gaps and the benchmark.

Thin views: forms validate input, `support.services` / `support.gaps` do the work. A state
conflict (already reviewed, already published) renders the page with 409; a Meko or Claude
failure renders it with 502 and leaves the state as it was. Successful posts redirect.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from dejafix.llm import ClaudeLLM, LLMError, StructuredLLM
from dejafix.meko import MekoError
from support import dashboard, memory
from support.benchmark import calibration_for, ground_truth, summaries_for
from support.forms import ApproveForm, GapDraftForm, RejectForm, gap_filter, ticket_filter
from support.gaps import dismiss_gap, publish_gap
from support.models import BenchmarkRun, KnowledgeGap, Resolution, Ticket
from support.presenters import pipeline
from support.services import ReviewError, approve_resolution, reject_resolution, resolve_ticket

PAGE_SIZE = 50
CONFLICT = 409
UPSTREAM_FAILURE = 502
BAD_REQUEST = 400


def llm_factory() -> StructuredLLM:
    # Module-level so tests can swap in the scripted Claude.
    return ClaudeLLM.from_settings()


def staff_required(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Logged-in staff only: anonymous users go to the login page, others get 403."""

    @login_required
    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)

    return wrapper


def _page(request: HttpRequest, queryset) -> Any:
    try:
        return Paginator(queryset, PAGE_SIZE).page(request.GET.get("page") or 1)
    except (EmptyPage, PageNotAnInteger) as exc:
        raise Http404("No such page.") from exc


# --- Dashboard --------------------------------------------------------------------------------


@require_GET
@staff_required
def dashboard_view(request: HttpRequest) -> HttpResponse:
    docs_run, meko_run = dashboard.latest_benchmark_pair()
    tickets = dashboard.ticket_stats()
    context: dict[str, Any] = {
        "tickets": tickets,
        "gaps": dashboard.gap_stats(),
        "quality": dashboard.context_quality(),
        "status_rows": [(value, label, tickets.by_status.get(value, 0)) for value, label in Ticket.Status.choices],
    }
    if docs_run and meko_run:
        docs, meko = summaries_for([docs_run, meko_run])
        context["benchmark"] = {"docs": docs, "meko": meko, "bars": _benchmark_bars(docs, meko)}
    return render(request, "support/dashboard.html", context)


def _benchmark_bars(docs, meko) -> list[dashboard.BarRow]:
    return dashboard.bar_rows(
        [
            ("Correct fixes", docs.correct, meko.correct, f"/{docs.core}"),
            ("Correct on repeats", docs.correct_repeats, meko.correct_repeats, f"/{docs.repeats}"),
            ("Avg tokens on repeats", docs.tokens_repeats_avg, meko.tokens_repeats_avg, ""),
            ("Tickets a human handled", docs.human_touches, meko.human_touches, f"/{docs.core}"),
        ]
    )


# --- Tickets ----------------------------------------------------------------------------------


@require_GET
@staff_required
def ticket_list(request: HttpRequest) -> HttpResponse:
    form = ticket_filter(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest("Unknown status or page.")
    tickets = Ticket.objects.select_related("resolution").order_by("id")
    if status := form.cleaned_data["status"]:
        tickets = tickets.filter(status=status)
    return render(
        request,
        "support/ticket_list.html",
        {"page": _page(request, tickets), "status": status, "statuses": Ticket.Status.choices},
    )


def _ticket_page(
    request: HttpRequest,
    ticket: Ticket,
    *,
    status: int = 200,
    error: str = "",
    reject_form: RejectForm | None = None,
) -> HttpResponse:
    resolution = Resolution.objects.filter(ticket=ticket).select_related("reviewed_by").first()
    runs = list(ticket.runs.order_by("id"))
    reused_from = reused_by = None
    if resolution and resolution.reused_memory_id:
        reused_from = (
            Resolution.objects.filter(promoted_memory_id=resolution.reused_memory_id)
            .select_related("ticket", "reviewed_by").first()
        )
    if resolution and resolution.promoted_memory_id:
        reused_by = list(
            Resolution.objects.filter(reused_memory_id=resolution.promoted_memory_id).select_related("ticket").order_by("id")
        )
    context = {
        "ticket": ticket,
        "resolution": resolution,
        "runs": runs,
        "stages": pipeline(ticket, resolution, runs),
        "max_step_tokens": max((run.total_tokens for run in runs), default=0),
        "total_tokens": sum(run.total_tokens for run in runs),
        "corrections_applied": next(
            (run.decision.get("corrections_applied") or [] for run in runs if run.step == "solve"), []
        ),
        "reused_from": reused_from,
        "reused_by": reused_by,
        "gaps": list(ticket.knowledge_gaps.order_by("id")),
        "approve_form": ApproveForm(),
        "reject_form": reject_form or RejectForm(),
        "error": error,
        "can_review": request.user.has_perm("support.change_resolution"),
        "can_resolve": request.user.has_perm("support.change_ticket"),
    }
    return render(request, "support/ticket_detail.html", context, status=status)


@require_GET
@staff_required
def ticket_detail(request: HttpRequest, ref: str) -> HttpResponse:
    return _ticket_page(request, get_object_or_404(Ticket, external_id=ref))


@require_POST
@staff_required
@permission_required("support.change_ticket", raise_exception=True)
def ticket_resolve(request: HttpRequest, ref: str) -> HttpResponse:
    ticket = get_object_or_404(Ticket, external_id=ref)
    if not ticket.is_resolvable:
        return _ticket_page(request, ticket, status=CONFLICT, error="This ticket was already resolved.")
    try:
        llm = llm_factory()
    except LLMError as exc:
        return _ticket_page(request, ticket, status=UPSTREAM_FAILURE, error=f"Claude is not available: {exc}")
    result = resolve_ticket(ticket, llm=llm)
    if result.error:
        return _ticket_page(request, result.ticket, status=UPSTREAM_FAILURE, error=f"The agents failed: {result.error}")
    messages.success(request, f"{ticket.external_id}: the agents finished ({result.ticket.get_status_display().lower()}).")
    return redirect("support:ticket_detail", ref=ticket.external_id)


def _resolution_for(ref: str) -> Resolution:
    return get_object_or_404(Resolution.objects.select_related("ticket"), ticket__external_id=ref)


@require_POST
@staff_required
@permission_required("support.change_resolution", raise_exception=True)
def resolution_approve(request: HttpRequest, ref: str) -> HttpResponse:
    resolution = _resolution_for(ref)
    form = ApproveForm(request.POST)
    if not form.is_valid():
        return _ticket_page(request, resolution.ticket, status=BAD_REQUEST, error="The note is too long.")
    try:
        approve_resolution(resolution, user=request.user, note=form.cleaned_data["note"])
    except ReviewError as exc:
        return _ticket_page(request, resolution.ticket, status=CONFLICT, error=str(exc))
    except MekoError as exc:
        return _ticket_page(request, resolution.ticket, status=UPSTREAM_FAILURE, error=f"Meko failed, nothing was shared: {exc}")
    messages.success(request, f"{ref}: approved and shared as collective memory.")
    return redirect("support:ticket_detail", ref=ref)


@require_POST
@staff_required
@permission_required("support.change_resolution", raise_exception=True)
def resolution_reject(request: HttpRequest, ref: str) -> HttpResponse:
    resolution = _resolution_for(ref)
    form = RejectForm(request.POST)
    if not form.is_valid():
        return _ticket_page(request, resolution.ticket, status=BAD_REQUEST, reject_form=form)
    try:
        reject_resolution(resolution, user=request.user, note=form.cleaned_data["note"])
    except ReviewError as exc:
        return _ticket_page(request, resolution.ticket, status=CONFLICT, error=str(exc))
    except MekoError as exc:
        return _ticket_page(request, resolution.ticket, status=UPSTREAM_FAILURE, error=f"Meko failed, nothing was stored: {exc}")
    messages.success(request, f"{ref}: rejected; your note is now a learning for the resolvers.")
    return redirect("support:ticket_detail", ref=ref)


# --- Shared memory ----------------------------------------------------------------------------


@require_GET
@staff_required
def memory_view(request: HttpRequest) -> HttpResponse:
    return render(request, "support/memory.html", {"memory": memory.overview()})


# --- Knowledge gaps ---------------------------------------------------------------------------


@require_GET
@staff_required
def gap_list(request: HttpRequest) -> HttpResponse:
    form = gap_filter(request.GET)
    if not form.is_valid():
        return HttpResponseBadRequest("Unknown status or page.")
    gaps = KnowledgeGap.objects.select_related("ticket").order_by("id")
    if status := form.cleaned_data["status"]:
        gaps = gaps.filter(status=status)
    return render(
        request,
        "support/gap_list.html",
        {"page": _page(request, gaps), "status": status, "statuses": KnowledgeGap.Status.choices},
    )


def _gap_page(request: HttpRequest, gap: KnowledgeGap, *, status: int = 200, error: str = "", form: GapDraftForm | None = None) -> HttpResponse:
    editable = gap.status in KnowledgeGap.EDITABLE_STATES
    context = {
        "gap": gap,
        "form": form or (GapDraftForm(instance=gap) if editable else None),
        "editable": editable,
        "publishable": gap.status in (KnowledgeGap.Status.DRAFTED, KnowledgeGap.Status.NEEDS_AUTHOR),
        "can_edit": request.user.has_perm("support.change_knowledgegap"),
        "error": error,
    }
    return render(request, "support/gap_detail.html", context, status=status)


def _gap(pk: int) -> KnowledgeGap:
    return get_object_or_404(KnowledgeGap.objects.select_related("ticket", "published_by"), pk=pk)


@require_GET
@staff_required
def gap_detail(request: HttpRequest, pk: int) -> HttpResponse:
    return _gap_page(request, _gap(pk))


@require_POST
@staff_required
@permission_required("support.change_knowledgegap", raise_exception=True)
def gap_save(request: HttpRequest, pk: int) -> HttpResponse:
    gap = _gap(pk)
    form = GapDraftForm(request.POST)
    if not form.is_valid():
        return _gap_page(request, gap, status=BAD_REQUEST, form=form)
    saved = KnowledgeGap.objects.filter(pk=gap.pk, status__in=KnowledgeGap.EDITABLE_STATES).update(
        draft_title=form.cleaned_data["draft_title"].strip(), draft_body=form.cleaned_data["draft_body"].strip()
    )
    if not saved:
        return _gap_page(request, gap, status=CONFLICT, error="Published or dismissed articles can't be edited.")
    messages.success(request, "Draft saved.")
    return redirect("support:gap_detail", pk=gap.pk)


@require_POST
@staff_required
@permission_required("support.change_knowledgegap", raise_exception=True)
def gap_publish(request: HttpRequest, pk: int) -> HttpResponse:
    gap = _gap(pk)
    try:
        publish_gap(gap, user=request.user)
    except ReviewError as exc:
        return _gap_page(request, _gap(pk), status=CONFLICT, error=str(exc))
    except MekoError as exc:
        return _gap_page(request, _gap(pk), status=UPSTREAM_FAILURE, error=f"Meko failed, nothing was published: {exc}")
    messages.success(request, "Published to the Meko knowledge base. Run verify_gaps once Meko has indexed it.")
    return redirect("support:gap_detail", pk=pk)


@require_POST
@staff_required
@permission_required("support.change_knowledgegap", raise_exception=True)
def gap_dismiss(request: HttpRequest, pk: int) -> HttpResponse:
    gap = _gap(pk)
    try:
        dismiss_gap(gap)
    except ReviewError as exc:
        return _gap_page(request, gap, status=CONFLICT, error=str(exc))
    messages.success(request, "Gap dismissed.")
    return redirect("support:gap_detail", pk=pk)


# --- Benchmark --------------------------------------------------------------------------------


@require_GET
@staff_required
def benchmark_view(request: HttpRequest) -> HttpResponse:
    docs_run, meko_run = dashboard.latest_benchmark_pair()
    if not (docs_run and meko_run):
        return render(request, "support/benchmark.html", {"empty": True})
    docs, meko = summaries_for([docs_run, meko_run])
    rows, threshold_range = calibration_for(meko_run)
    results = {
        run.name: {result.ticket_ref: result for result in run.results.all()} for run in (docs_run, meko_run)
    }
    tickets = [
        {"ticket": ticket, "docs": results[docs_run.name].get(ticket.ref), "meko": results[meko_run.name].get(ticket.ref)}
        for ticket in ground_truth().tickets
    ]
    context = {
        "docs": docs,
        "meko": meko,
        "bars": _benchmark_bars(docs, meko),
        "calibration": rows,
        "threshold_range": threshold_range,
        "tickets": tickets,
        "admin_results_url": reverse("admin:support_benchmarkresult_changelist"),
    }
    return render(request, "support/benchmark.html", context)

