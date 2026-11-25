from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from dejafix.tests.stateful_meko import build_stateful_meko
from support.models import Resolution, Ticket

CHANGELIST = "admin:support_resolution_changelist"


def make_resolution(ref: str, **overrides) -> Resolution:
    ticket = Ticket.objects.create(external_id=ref, subject="s", body="b", customer="c", status=Ticket.Status.AWAITING_REVIEW)
    fields = dict(
        resolver_agent="dejafix-resolver-a", source="new", category="payouts", problem_signature="Payout pending",
        fix_summary="Explain T+2.", fix_steps=["Open Payouts > Calendar."], confidence=0.9, reviewer_verdict="approve_for_human",
    )
    fields.update(overrides)
    return Resolution.objects.create(ticket=ticket, **fields)


@override_settings(DEJAFIX_CURATOR_AGENT="dejafix-curator")
class ResolutionAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.staff = user_model.objects.create_superuser("admin", password="unused-in-tests")
        cls.plain = user_model.objects.create_user("plain", password="unused-in-tests")

    def test_anonymous_and_non_staff_are_redirected_to_login(self):
        self.assertEqual(self.client.get(reverse(CHANGELIST)).status_code, 302)
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(reverse(CHANGELIST)).status_code, 302)

    def test_changelist_query_count_does_not_grow_with_rows(self):
        self.client.force_login(self.staff)
        make_resolution("T1")
        # session, user, 2 counts, the joined page query, 2 filter-choice queries
        with self.assertNumQueries(7):
            self.client.get(reverse(CHANGELIST))
        for index in range(2, 12):
            make_resolution(f"T{index}")
        with self.assertNumQueries(7):
            self.client.get(reverse(CHANGELIST))

    def test_approve_action_promotes_through_curator(self):
        server, world = build_stateful_meko()
        resolution = make_resolution("T1")
        self.client.force_login(self.staff)
        with mock.patch("support.services.default_session_factory", world.session_factory(server)):
            response = self.client.post(
                reverse(CHANGELIST), {"action": "approve_selected", "_selected_action": [resolution.pk]}, follow=True
            )
        self.assertContains(response, "T1: approved and promoted.")
        resolution.refresh_from_db()
        self.assertEqual(resolution.review_status, "approved")
        self.assertEqual(world.memory_by_id(resolution.promoted_memory_id).agent_id, "dejafix-curator")

    def test_reject_action_without_note_shows_error_and_changes_nothing(self):
        server, world = build_stateful_meko()
        resolution = make_resolution("T1")
        self.client.force_login(self.staff)
        with mock.patch("support.services.default_session_factory", world.session_factory(server)):
            response = self.client.post(
                reverse(CHANGELIST), {"action": "reject_selected", "_selected_action": [resolution.pk]}, follow=True
            )
        self.assertContains(response, "needs a note")
        resolution.refresh_from_db()
        self.assertEqual(resolution.review_status, "pending")
        self.assertEqual(world.memories, [])
