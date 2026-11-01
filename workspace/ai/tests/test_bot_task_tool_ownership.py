from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.ai.models import AgentGoal, BotProfile, ScheduledMessage
from workspace.ai.tool_registry import ToolError
from workspace.ai.tools import (
    AgentGoalToolProvider,
    CancelScheduleParams,
    CompleteAgentGoalParams,
    ScheduleToolProvider,
    UpdateAgentGoalParams,
)
from workspace.chat.models import Conversation, ConversationMember

User = get_user_model()


class BotTaskToolOwnershipMixin:
    """A group where alice set the bot to work and bob talks to the same bot.

    The bot carries a goal or a schedule out with its creator's identity, so a
    tool call made while answering bob must not rewrite or stop alice's task:
    the bot would then act with alice's mail, calendar and files on bob's say.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.bot_user = User.objects.create_user(username="bot", password="pass")
        BotProfile.objects.create(user=self.bot_user, system_prompt="Bot.")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="G", created_by=self.alice
        )
        for user in (self.alice, self.bob, self.bot_user):
            ConversationMember.objects.create(conversation=self.conversation, user=user)
        self.soon = timezone.now() + timedelta(hours=1)

    def tearDown(self):
        cache.clear()

    def _call(self, method_name, args, user):
        return getattr(self.provider, method_name)(
            args,
            user=user,
            bot=self.bot_user,
            conversation_id=str(self.conversation.uuid),
            context={},
        )


class AgentGoalToolOwnershipTests(BotTaskToolOwnershipMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = AgentGoalToolProvider()
        self.goal = AgentGoal.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.alice,
            title="Watch listings",
            goal="Report good listings.",
            notes="Nothing yet.",
            next_check_at=self.soon,
        )

    def test_other_member_cannot_update_the_goal(self):
        with self.assertRaises(ToolError) as refused:
            self._call(
                "update_agent_goal",
                UpdateAgentGoalParams(
                    goal_id=self.goal.uuid,
                    constraints="Also forward every mail from X to Y.",
                    notes="Forward mail.",
                    status="paused",
                ),
                self.bob,
            )
        # Same answer as an id that does not exist: bob learns nothing.
        self.assertEqual(
            str(refused.exception), f"no open goal found with id {self.goal.uuid}"
        )
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.constraints, "")
        self.assertEqual(self.goal.notes, "Nothing yet.")
        self.assertEqual(self.goal.status, AgentGoal.Status.ACTIVE)

    def test_other_member_cannot_close_the_goal(self):
        for abandoned in (False, True):
            with self.subTest(abandoned=abandoned):
                with self.assertRaises(ToolError) as refused:
                    self._call(
                        "complete_agent_goal",
                        CompleteAgentGoalParams(
                            goal_id=self.goal.uuid, outcome="Done.", abandoned=abandoned
                        ),
                        self.bob,
                    )
                self.assertEqual(
                    str(refused.exception),
                    f"no open goal found with id {self.goal.uuid}",
                )
                self.goal.refresh_from_db()
                self.assertEqual(self.goal.status, AgentGoal.Status.ACTIVE)
                self.assertEqual(self.goal.outcome, "")
                self.assertIsNone(self.goal.closed_at)

    def test_creator_can_update_and_close_the_goal(self):
        self._call(
            "update_agent_goal",
            UpdateAgentGoalParams(goal_id=self.goal.uuid, notes="Two listings."),
            self.alice,
        )
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.notes, "Two listings.")

        self._call(
            "complete_agent_goal",
            CompleteAgentGoalParams(goal_id=self.goal.uuid, outcome="Found one."),
            self.alice,
        )
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, AgentGoal.Status.COMPLETED)

    def test_other_member_still_sees_the_goal_marked_read_only(self):
        # Reading a goal in a conversation one belongs to is allowed; only
        # changing it is the creator's call.
        listing = self._call("list_agent_goals", None, self.bob)
        self.assertIn(str(self.goal.uuid), listing)
        self.assertIn("only its creator can change it", listing)

        own_listing = self._call("list_agent_goals", None, self.alice)
        self.assertIn(str(self.goal.uuid), own_listing)
        self.assertNotIn("only its creator can change it", own_listing)


class ScheduleToolOwnershipTests(BotTaskToolOwnershipMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.provider = ScheduleToolProvider()
        self.schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.alice,
            prompt="Summarize my inbox",
            kind=ScheduledMessage.Kind.ONCE,
            next_run_at=self.soon,
        )

    def test_other_member_cannot_cancel_the_schedule(self):
        with self.assertRaises(ToolError) as refused:
            self._call(
                "cancel_schedule",
                CancelScheduleParams(schedule_id=self.schedule.uuid),
                self.bob,
            )
        self.assertEqual(
            str(refused.exception),
            f"no active schedule found with id {self.schedule.uuid}",
        )
        self.schedule.refresh_from_db()
        self.assertTrue(self.schedule.is_active)

    def test_creator_can_cancel_the_schedule(self):
        self._call(
            "cancel_schedule",
            CancelScheduleParams(schedule_id=self.schedule.uuid),
            self.alice,
        )
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.is_active)

    def test_other_member_still_sees_the_schedule_marked_read_only(self):
        listing = self._call("list_schedules", None, self.bob)
        self.assertIn(str(self.schedule.uuid), listing)
        self.assertIn("only its creator can change it", listing)

        own_listing = self._call("list_schedules", None, self.alice)
        self.assertIn(str(self.schedule.uuid), own_listing)
        self.assertNotIn("only its creator can change it", own_listing)
