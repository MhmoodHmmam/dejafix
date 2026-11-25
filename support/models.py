"""Local records of tickets and of what the agents decided.

Meko holds the shared context (memory, knowledge, conversations, traces). These tables hold
what the dashboard, the human review step and the benchmark need to query quickly.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.db.models import Q

MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 5000
MAX_NOTE_LENGTH = 2000
AGENT_NAME_LENGTH = 60
MEKO_ID_LENGTH = 64


class Ticket(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        AWAITING_REVIEW = "awaiting_review", "Awaiting human review"
        ESCALATED = "escalated", "Escalated"
        CLOSED = "closed", "Closed"
        FAILED = "failed", "Failed (retryable)"

    external_id = models.CharField(max_length=40, unique=True)
    subject = models.CharField(max_length=MAX_SUBJECT_LENGTH)
    body = models.TextField(max_length=MAX_BODY_LENGTH)
    customer = models.CharField(max_length=120)
    # Ground truth for synthetic tickets only; never shown to the agents.
    expected_issue = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    failure_reason = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        indexes = [models.Index(fields=["status", "id"])]

    def __str__(self) -> str:
        return f"{self.external_id}: {self.subject}"

    @property
    def is_resolvable(self) -> bool:
        return self.status in {self.Status.NEW, self.Status.FAILED}


class Resolution(models.Model):
    class Source(models.TextChoices):
        REUSED = "reused", "Reused an approved fix"
        NEW = "new", "Solved from knowledge"

    class Review(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    ticket = models.OneToOneField(Ticket, on_delete=models.CASCADE, related_name="resolution")
    resolver_agent = models.CharField(max_length=AGENT_NAME_LENGTH)
    source = models.CharField(max_length=10, choices=Source.choices)
    category = models.CharField(max_length=40)
    problem_signature = models.CharField(max_length=200)
    fix_summary = models.CharField(max_length=300)
    fix_steps = models.JSONField(default=list)
    customer_reply = models.TextField(blank=True)
    confidence = models.FloatField()
    reused_memory_id = models.CharField(max_length=MEKO_ID_LENGTH, blank=True)
    reviewer_verdict = models.CharField(max_length=20)
    reviewer_reason = models.CharField(max_length=500, blank=True)
    review_status = models.CharField(max_length=10, choices=Review.choices, default=Review.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    human_note = models.TextField(max_length=MAX_NOTE_LENGTH, blank=True)
    promoted_memory_id = models.CharField(max_length=MEKO_ID_LENGTH, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(source="reused") | ~Q(reused_memory_id=""),
                name="resolution_reused_has_memory_id",
            ),
            models.CheckConstraint(
                condition=Q(review_status="pending") | Q(reviewed_at__isnull=False),
                name="resolution_reviewed_has_timestamp",
            ),
            models.CheckConstraint(
                condition=Q(confidence__gte=0) & Q(confidence__lte=1),
                name="resolution_confidence_0_1",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.ticket.external_id} ({self.source}, {self.review_status})"


class AgentRun(models.Model):
    """One agent step on one ticket, mirroring the turn written to Meko."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="runs")
    step = models.CharField(max_length=30)
    agent = models.CharField(max_length=AGENT_NAME_LENGTH)
    meko_conversation_id = models.CharField(max_length=MEKO_ID_LENGTH, blank=True)
    decision = models.JSONField(default=dict)
    reasoning = models.TextField(blank=True)
    model = models.CharField(max_length=60, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(default=0)
    meko_calls = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["ticket_id", "id"]
        constraints = [models.UniqueConstraint(fields=["ticket", "step"], name="agentrun_one_per_ticket_step")]

    def __str__(self) -> str:
        return f"{self.ticket.external_id} · {self.step} ({self.agent})"

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens
