"""Staff-only review screens (the dashboard comes in week 5)."""

from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from dejafix.meko import MekoError
from support.models import AgentRun, Resolution, Ticket
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
        "review_status", "reviewed_by", "reviewed_at", "promoted_memory_id", "created_at",
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
