"""Bots and deactivated accounts can never be invited to an event or a poll."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.models import BotProfile
from workspace.ai.tool_registry import ToolError
from workspace.calendar.ai_tools import _resolve_usernames
from workspace.calendar.models import Event, EventMember, Poll, PollInvitee
from workspace.calendar.services.recurrence_rule import apply_rule
from workspace.notifications.models import Notification

from .test_calendar import CalendarTestMixin

User = get_user_model()


class UnpickableInviteesTests(CalendarTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.bot = User.objects.create_user(username="helper-bot", password="x")
        BotProfile.objects.create(user=self.bot)
        self.gone = User.objects.create_user(
            username="gone", password="x", is_active=False
        )
        self.unpickable = {self.bot.id, self.gone.id}
        self.client.force_authenticate(self.owner)

    def _member_ids(self, event):
        return set(
            EventMember.objects.filter(event=event, user__isnull=False).values_list(
                "user_id", flat=True
            )
        )

    def test_create_ignores_bots_and_inactive_accounts(self):
        start = timezone.now() + timedelta(days=2)
        resp = self.client.post(
            "/api/v1/events",
            {
                "calendar_id": str(self.calendar.uuid),
                "title": "Kickoff",
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "member_ids": [self.member.id, self.bot.id, self.gone.id],
            },
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.content)
        event = Event.objects.get(uuid=resp.data["uuid"])
        self.assertEqual(self._member_ids(event), {self.member.id})
        self.assertFalse(
            Notification.objects.filter(recipient_id__in=self.unpickable).exists()
        )

    def test_update_ignores_bots_and_inactive_accounts(self):
        resp = self.client.put(
            f"/api/v1/events/{self.event.uuid}",
            {"member_ids": [self.member.id, self.bot.id, self.gone.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(self._member_ids(self.event), {self.member.id})
        self.assertFalse(
            Notification.objects.filter(recipient_id__in=self.unpickable).exists()
        )

    def test_editing_one_occurrence_ignores_bots_and_inactive_accounts(self):
        apply_rule(self.event, "FREQ=WEEKLY;COUNT=4")
        self.event.save()
        occurrence = (self.event.start + timedelta(weeks=1)).isoformat()

        resp = self.client.put(
            f"/api/v1/events/{self.event.uuid}",
            {
                "scope": "this",
                "original_start": occurrence,
                "member_ids": [self.member.id, self.bot.id, self.gone.id],
            },
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        exception = Event.objects.get(recurrence_parent=self.event)
        self.assertEqual(self._member_ids(exception), {self.member.id})

    def test_poll_invite_ignores_bots_and_inactive_accounts(self):
        poll = Poll.objects.create(title="Lunch", created_by=self.owner)

        resp = self.client.post(
            f"/api/v1/polls/{poll.uuid}/invite",
            {"user_ids": [self.member.id, self.bot.id, self.gone.id]},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        self.assertEqual(
            set(
                PollInvitee.objects.filter(poll=poll).values_list("user_id", flat=True)
            ),
            {self.member.id},
        )

    def test_assistant_cannot_invite_a_bot_by_username(self):
        with self.assertRaises(ToolError):
            _resolve_usernames(["member", "helper-bot"])
        self.assertEqual(_resolve_usernames(["member"]), [self.member.id])
