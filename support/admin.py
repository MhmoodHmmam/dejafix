"""Staff-only review screens (the dashboard comes in week 5)."""

from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from dejafix.meko import MekoError
from support.gaps import dismiss_gap, publish_gap
from support.models import AgentRun, KnowledgeGap, Resolution, Ticket
from support.services import ReviewError, approve_resolution, reject_resolution


class AgentRunInline(admin.TabularInline):
    model = AgentRun
    extra = 0
    can_delete = False
    fields = ("step", "agent", "reasoning", "input_tokens", "output_tokens", "latency_ms", "meko_calls")
    readonly_fields = fields

    def has_add_permission(self, request: HttpRequest, obj: Ticket | None = None) -> bool:
        return False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("external_id", "subject", "customer", "status", "updated_at")
    list_filter = ("status",)
    search_fields = ("external_id", "subject", "customer")
    readonly_fields = ("status", "failure_reason", "created_at", "updated_at")
    inlines = [AgentRunInline]
    list_per_page = 50


@admin.register(Resolution)
class ResolutionAdmin(admin.ModelAdmin):
    list_display = ("ticket", "source", "category", "review_status", "reviewer_verdict", "resolver_agent", "confidence")
    list_filter = ("review_status", "source", "category", "reviewer_verdict")
    list_select_related = ("ticket",)
    search_fields = ("ticket__external_id", "problem_signature", "fix_summary")
    actions = ["approve_selected", "reject_selected"]
    list_per_page = 50
    # Everything except the reviewer's note is produced by the agents and stays read-only.
    readonly_fields = (
        "ticket", "resolver_agent", "source", "category", "problem_signature", "fix_summary", "fix_steps",
        "customer_reply", "confidence", "reused_memory_id", "reviewer_verdict", "reviewer_reason",
        "review_status", "reviewed_by", "reviewed_at", "promoted_memory_id", "case_file_hash", "created_at",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    @admin.action(description="Approve and share as collective memory", permissions=["change"])
    def approve_selected(self, request: HttpRequest, queryset: QuerySet[Resolution]) -> None:
        for resolution in queryset.select_related("ticket"):
            try:
                approve_resolution(resolution, user=request.user, note=resolution.human_note)
            except (ReviewError, MekoError) as exc:
                self.message_user(request, f"{resolution.ticket.external_id}: {exc}", messages.ERROR)
            else:
                self.message_user(request, f"{resolution.ticket.external_id}: approved and promoted.")

    @admin.action(description="Reject (uses the reviewer note)", permissions=["change"])
    def reject_selected(self, request: HttpRequest, queryset: QuerySet[Resolution]) -> None:
        for resolution in queryset.select_related("ticket"):
            try:
                reject_resolution(resolution, user=request.user, note=resolution.human_note)
            except (ReviewError, MekoError) as exc:
                self.message_user(request, f"{resolution.ticket.external_id}: {exc}", messages.ERROR)
            else:
                self.message_user(request, f"{resolution.ticket.external_id}: rejected, note saved as a learning.")


@admin.register(KnowledgeGap)
class KnowledgeGapAdmin(admin.ModelAdmin):
    """Review the librarian's drafts. Only the title and body are editable, and only before publishing."""

    list_display = ("id", "short_query", "status", "band_before", "score_before", "band_after", "score_after", "ticket", "times_asked")
    list_filter = ("status", "band_before")
    list_select_related = ("ticket",)
    search_fields = ("query", "draft_title")
    actions = ["publish_selected", "dismiss_selected"]
    list_per_page = 50
    fields = (
        "query", "status", "ticket", "agent", "reason", "band_before", "score_before", "times_asked",
        "draft_reasoning", "draft_title", "draft_body",
        "document_key", "published_by", "published_at", "band_after", "score_after", "source_after", "kb_rank_after",
        "verified_at", "test_prompt_id", "log_id",
    )
    _always_readonly = tuple(field for field in fields if field not in ("draft_title", "draft_body"))

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def get_readonly_fields(self, request: HttpRequest, obj: KnowledgeGap | None = None) -> tuple[str, ...]:
        if obj is not None and obj.status not in KnowledgeGap.EDITABLE_STATES:
            return self.fields
        return self._always_readonly

    @admin.display(description="Weak search")
    def short_query(self, gap: KnowledgeGap) -> str:
        return gap.query[:80]

    @admin.action(description="Publish to the Meko knowledge base", permissions=["change"])
    def publish_selected(self, request: HttpRequest, queryset: QuerySet[KnowledgeGap]) -> None:
        for gap in queryset:
            try:
                publish_gap(gap, user=request.user)
            except (ReviewError, MekoError) as exc:
                self.message_user(request, f"Gap {gap.pk}: {exc}", messages.ERROR)
            else:
                self.message_user(request, f"Gap {gap.pk}: published. Run verify_gaps once Meko has indexed it.")

    @admin.action(description="Dismiss (no article needed)", permissions=["change"])
    def dismiss_selected(self, request: HttpRequest, queryset: QuerySet[KnowledgeGap]) -> None:
        for gap in queryset:
            try:
                dismiss_gap(gap)
            except ReviewError as exc:
                self.message_user(request, f"Gap {gap.pk}: {exc}", messages.ERROR)
            else:
                self.message_user(request, f"Gap {gap.pk}: dismissed.")
