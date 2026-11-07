"""A PATCH or DELETE racing the worker that runs the same schedule or goal.

The request loads the row, then writes it back while the dispatcher, the
check-in worker and the agent's own tools write the same row. Each test lands the worker's write between
the request's load and its write, through the permission check the view runs
in between, and asserts the worker's write survives.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.models import AgentGoal, BotProfile, ScheduledMessage
from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services.bot_tasks import can_manage_bot_task

User = get_user_model()


def run_meanwhile(write):
    """A permission check that lets *write* land before allowing the request."""

    def check(user, task):
        write()
        return can_manage_bot_task(user, task)

    return check


class BotTaskRaceMixin:
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="racer", password="pass")
        self.bot = User.objects.create_user(username="racebot", password="pass")
        BotProfile.objects.create(user=self.bot, created_by=self.user)
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        for user in (self.user, self.bot):
            ConversationMember.objects.create(conversation=self.conversation, user=user)
        self.client.force_authenticate(self.user)


class ScheduledMessagePatchRaceTests(BotTaskRaceMixin, APITestCase):
    PERMISSION_CHECK = "workspace.chat.views.scheduled.can_manage_bot_task"

    def _schedule(self, **fields):
        return ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot,
            created_by=self.user,
            prompt="Remind me",
            **fields,
        )

    def _url(self, schedule):
        return (
            f"/api/v1/chat/conversations/{self.conversation.uuid}/schedules/"
            f"{schedule.uuid}"
        )

    def _fire_once(self, schedule):
        """What the worker writes when a one-time message goes out."""

        def write():
            ScheduledMessage.objects.filter(pk=schedule.pk).update(
                is_active=False, last_run_at=timezone.now()
            )

        return write

    def test_a_one_time_message_that_fired_does_not_come_back(self):
        fire_at = timezone.now() + timedelta(minutes=1)
        once = self._schedule(
            kind=ScheduledMessage.Kind.ONCE, scheduled_at=fire_at, next_run_at=fire_at
        )

        with patch(self.PERMISSION_CHECK, run_meanwhile(self._fire_once(once))):
            resp = self.client.patch(
                self._url(once), {"prompt": "Remind me louder"}, format="json"
            )

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        once.refresh_from_db()
        self.assertFalse(once.is_active)
        self.assertEqual(once.prompt, "Remind me")

    def test_a_reschedule_does_not_reactivate_a_message_that_fired(self):
        fire_at = timezone.now() + timedelta(minutes=1)
        once = self._schedule(
            kind=ScheduledMessage.Kind.ONCE, scheduled_at=fire_at, next_run_at=fire_at
        )
        later = timezone.now() + timedelta(days=1)

        with patch(self.PERMISSION_CHECK, run_meanwhile(self._fire_once(once))):
            self.client.patch(
                self._url(once), {"scheduled_at": later.isoformat()}, format="json"
            )

        once.refresh_from_db()
        self.assertFalse(once.is_active)

    def test_a_prompt_edit_keeps_the_run_time_the_worker_advanced(self):
        due = timezone.now() - timedelta(minutes=1)
        daily = self._schedule(
            kind=ScheduledMessage.Kind.RECURRING,
            recurrence_unit=ScheduledMessage.RecurrenceUnit.DAYS,
            recurrence_interval=1,
            next_run_at=due,
        )
        tomorrow = due + timedelta(days=1)

        def advance():
            ScheduledMessage.objects.filter(pk=daily.pk).update(
                next_run_at=tomorrow, last_run_at=timezone.now()
            )

        with patch(self.PERMISSION_CHECK, run_meanwhile(advance)):
            resp = self.client.patch(
                self._url(daily), {"prompt": "Remind me daily"}, format="json"
            )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        daily.refresh_from_db()
        self.assertEqual(daily.prompt, "Remind me daily")
        # Written back as loaded, the past run time would fire it again.
        self.assertEqual(daily.next_run_at, tomorrow)
        self.assertEqual(datetime.fromisoformat(resp.data["next_run_at"]), tomorrow)


class AgentGoalRequestRaceTests(BotTaskRaceMixin, APITestCase):
    PERMISSION_CHECK = "workspace.chat.views.goals.can_manage_bot_task"

    def setUp(self):
        super().setUp()
        self.goal = AgentGoal.objects.create(
            conversation=self.conversation,
            bot=self.bot,
            created_by=self.user,
            title="Watch listings",
            goal="Report good listings.",
            status=AgentGoal.Status.PAUSED,
            next_check_at=timezone.now() + timedelta(hours=1),
        )
        self.url = (
            f"/api/v1/chat/conversations/{self.conversation.uuid}/goals/"
            f"{self.goal.uuid}"
        )

    def _agent_completes(self):
        AgentGoal.objects.filter(pk=self.goal.pk).update(
            status=AgentGoal.Status.COMPLETED,
            outcome="Found the flat.",
            closed_at=timezone.now(),
        )

    def test_a_resume_does_not_reopen_a_goal_the_agent_closed(self):
        with patch(self.PERMISSION_CHECK, run_meanwhile(self._agent_completes)):
            resp = self.client.patch(self.url, {"status": "active"}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, AgentGoal.Status.COMPLETED)

    def test_a_notes_edit_keeps_the_check_in_the_worker_parked(self):
        parked = timezone.now() + timedelta(days=1)

        def check_in():
            AgentGoal.objects.filter(pk=self.goal.pk).update(
                next_check_at=parked, check_count=1
            )

        with patch(self.PERMISSION_CHECK, run_meanwhile(check_in)):
            resp = self.client.patch(self.url, {"notes": "Lyon only."}, format="json")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.notes, "Lyon only.")
        self.assertEqual(self.goal.next_check_at, parked)
        self.assertEqual(self.goal.check_count, 1)

    def test_a_stop_does_not_overwrite_the_agent_closing_the_goal(self):
        with patch(self.PERMISSION_CHECK, run_meanwhile(self._agent_completes)):
            resp = self.client.delete(self.url)

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, AgentGoal.Status.COMPLETED)
        self.assertEqual(self.goal.outcome, "Found the flat.")
