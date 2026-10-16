import importlib
from datetime import timedelta
from types import SimpleNamespace

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from workspace.ai.models import AgentGoal, BotProfile, ScheduledMessage
from workspace.chat.models import Conversation, ConversationMember

User = get_user_model()

migration = importlib.import_module(
    "workspace.chat.migrations.0031_bots_leave_group_conversations"
)


class BotsLeaveGroupConversationsMigrationTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.bot = User.objects.create_user(username="bot")
        BotProfile.objects.create(user=self.bot, is_public=True)

        self.group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Team", created_by=self.alice
        )
        for user in (self.alice, self.bob, self.bot):
            ConversationMember.objects.create(conversation=self.group, user=user)

        self.dm = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.alice
        )
        for user in (self.alice, self.bot):
            ConversationMember.objects.create(conversation=self.dm, user=user)

    def _forwards(self):
        migration.forwards(apps, SimpleNamespace(connection=connection))

    def _member(self, conversation, user):
        return ConversationMember.objects.get(conversation=conversation, user=user)

    def _goal(self, conversation, status=AgentGoal.Status.ACTIVE):
        return AgentGoal.objects.create(
            conversation=conversation,
            bot=self.bot,
            created_by=self.alice,
            title="Watch my inbox",
            goal="Tell me about important mail.",
            status=status,
            next_check_at=timezone.now() + timedelta(hours=1),
        )

    def _schedule(self, conversation):
        return ScheduledMessage.objects.create(
            conversation=conversation,
            bot=self.bot,
            created_by=self.alice,
            prompt="Summarize my unread mail",
            kind=ScheduledMessage.Kind.RECURRING,
            recurrence_unit=ScheduledMessage.RecurrenceUnit.DAYS,
            recurrence_interval=1,
            next_run_at=timezone.now() + timedelta(hours=1),
        )

    def test_the_bot_leaves_the_group_and_stays_in_the_dm(self):
        self._forwards()

        self.assertIsNotNone(self._member(self.group, self.bot).left_at)
        self.assertIsNone(self._member(self.dm, self.bot).left_at)

    def test_the_people_stay_in_the_group(self):
        self._forwards()

        self.assertIsNone(self._member(self.group, self.alice).left_at)
        self.assertIsNone(self._member(self.group, self.bob).left_at)

    def test_open_goals_of_a_group_are_abandoned_and_dm_goals_kept(self):
        active = self._goal(self.group)
        paused = self._goal(self.group, status=AgentGoal.Status.PAUSED)
        completed = self._goal(self.group, status=AgentGoal.Status.COMPLETED)
        in_dm = self._goal(self.dm)

        self._forwards()

        for goal in (active, paused):
            goal.refresh_from_db()
            self.assertEqual(goal.status, AgentGoal.Status.ABANDONED)
            self.assertTrue(goal.outcome)
            self.assertIsNotNone(goal.closed_at)
        completed.refresh_from_db()
        self.assertEqual(completed.status, AgentGoal.Status.COMPLETED)
        in_dm.refresh_from_db()
        self.assertEqual(in_dm.status, AgentGoal.Status.ACTIVE)

    def test_schedules_of_a_group_are_deactivated_and_dm_schedules_kept(self):
        in_group = self._schedule(self.group)
        in_dm = self._schedule(self.dm)

        self._forwards()

        in_group.refresh_from_db()
        in_dm.refresh_from_db()
        self.assertFalse(in_group.is_active)
        self.assertTrue(in_dm.is_active)

    def test_a_bot_that_already_left_keeps_its_leaving_date(self):
        left_at = timezone.now() - timedelta(days=3)
        ConversationMember.objects.filter(
            conversation=self.group, user=self.bot
        ).update(left_at=left_at)

        self._forwards()

        self.assertEqual(self._member(self.group, self.bot).left_at, left_at)
