"""The agent's goal tools racing the user closing the same goal.

A check-in runs for minutes. The tool loads the goal, the user stops it from
the chat, then the tool writes: each test lands the user's stop between the
tool's load and its write.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.ai.models import AgentGoal, BotProfile
from workspace.ai.tool_registry import ToolError
from workspace.ai.tools import (
    AgentGoalToolProvider,
    CompleteAgentGoalParams,
    UpdateAgentGoalParams,
)
from workspace.chat.models import Conversation, ConversationMember

User = get_user_model()


class AgentGoalToolRaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="goalracer", password="pass")
        self.bot = User.objects.create_user(username="goalbot", password="pass")
        BotProfile.objects.create(user=self.bot, system_prompt="Bot.")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        for user in (self.user, self.bot):
            ConversationMember.objects.create(conversation=self.conversation, user=user)
        self.goal = AgentGoal.objects.create(
            conversation=self.conversation,
            bot=self.bot,
            created_by=self.user,
            title="Watch listings",
            goal="Report good listings.",
            status=AgentGoal.Status.PAUSED,
            next_check_at=timezone.now() + timedelta(hours=1),
        )
        self.context = {}

    def tearDown(self):
        cache.clear()

    def _user_stops_after_load(self):
        """The goal the tool loads, abandoned by the user right after."""
        load = AgentGoalToolProvider._open_goal

        def load_then_stop(*args):
            goal = load(*args)
            AgentGoal.objects.filter(pk=self.goal.pk).update(
                status=AgentGoal.Status.ABANDONED,
                outcome="Stopped by the user.",
                closed_at=timezone.now(),
            )
            return goal

        return patch.object(
            AgentGoalToolProvider, "_open_goal", staticmethod(load_then_stop)
        )

    def _call(self, name, args):
        return getattr(AgentGoalToolProvider(), name)(
            args,
            user=self.user,
            bot=self.bot,
            conversation_id=str(self.conversation.uuid),
            context=self.context,
        )

    def _assert_still_stopped(self):
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, AgentGoal.Status.ABANDONED)
        self.assertEqual(self.goal.outcome, "Stopped by the user.")

    def test_a_resume_does_not_reopen_a_goal_the_user_stopped(self):
        with self._user_stops_after_load(), self.assertRaises(ToolError):
            self._call(
                "update_agent_goal",
                UpdateAgentGoalParams(goal_id=self.goal.uuid, status="active"),
            )

        self._assert_still_stopped()
        # Otherwise the check-in would deliver its messages as if the agent
        # had changed the goal itself.
        self.assertNotIn("agent_goal_changed", self.context)

    def test_completing_does_not_overwrite_the_user_stopping(self):
        with self._user_stops_after_load(), self.assertRaises(ToolError):
            self._call(
                "complete_agent_goal",
                CompleteAgentGoalParams(goal_id=self.goal.uuid, outcome="Done."),
            )

        self._assert_still_stopped()
        self.assertNotIn("agent_goal_changed", self.context)
