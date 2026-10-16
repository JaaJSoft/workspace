from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.ai.models import AgentGoal, AITask, BotProfile, ScheduledMessage
from workspace.ai.services.bot_access import run_refusal
from workspace.chat.models import Conversation, ConversationMember, Message

User = get_user_model()


def _llm_reply(content):
    return {
        "content": content,
        "tool_calls": None,
        "message": MagicMock(content=content, tool_calls=None, to_dict=lambda: {}),
        "model": "gpt-4o-mini",
        "prompt_tokens": 10,
        "completion_tokens": 5,
    }


class BotAccessFixture(TestCase):
    """A group with a private bot that the creator reaches through a group."""

    def setUp(self):
        self.creator = User.objects.create_user(username="creator", password="x")
        self.other = User.objects.create_user(username="other", password="x")
        self.bot_user = User.objects.create_user(username="privbot")
        self.bot_profile = BotProfile.objects.create(
            user=self.bot_user, system_prompt="Bot.", is_public=False
        )
        self.team = Group.objects.create(name="bot-users")
        self.bot_profile.allowed_groups.add(self.team)
        self.creator.groups.add(self.team)
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.creator, title="g"
        )
        for member in (self.creator, self.other, self.bot_user):
            ConversationMember.objects.create(
                conversation=self.conversation, user=member
            )

    def tearDown(self):
        cache.clear()

    def leave(self, user):
        ConversationMember.objects.filter(
            conversation=self.conversation, user=user
        ).update(left_at=timezone.now())

    def deactivate(self, user):
        User.objects.filter(pk=user.pk).update(is_active=False)

    def revoke_bot(self, user):
        user.groups.remove(self.team)

    def bot_messages(self):
        return Message.objects.filter(
            conversation=self.conversation, author=self.bot_user
        )


class RunRefusalTests(BotAccessFixture):
    def test_allowed_creator_is_not_refused(self):
        self.assertIsNone(
            run_refusal(self.bot_profile, self.creator, self.conversation.pk)
        )

    def test_each_lost_condition_is_refused(self):
        self.assertEqual(
            run_refusal(self.bot_profile, None, self.conversation.pk), "no_user"
        )
        self.assertEqual(
            run_refusal(self.bot_profile, self.other, self.conversation.pk),
            "bot_not_accessible",
        )
        self.leave(self.creator)
        self.assertEqual(
            run_refusal(self.bot_profile, self.creator, self.conversation.pk),
            "not_a_member",
        )
        self.deactivate(self.creator)
        self.creator.refresh_from_db()
        self.assertEqual(
            run_refusal(self.bot_profile, self.creator, self.conversation.pk),
            "user_inactive",
        )


@patch("workspace.ai.harness.model.call_llm")
class ScheduledResponseAccessTests(BotAccessFixture):
    def _schedule(self):
        return ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.creator,
            prompt="Summarize my unread mail for the group",
            kind=ScheduledMessage.Kind.RECURRING,
            recurrence_unit=ScheduledMessage.RecurrenceUnit.DAYS,
            recurrence_interval=1,
            next_run_at=timezone.now() - timedelta(minutes=1),
        )

    def _assert_refused_and_deactivated(self, mock_llm, reason):
        from workspace.ai.tasks.scheduled import generate_scheduled_response

        mock_llm.return_value = _llm_reply("Here is the creator's private mail.")
        schedule = self._schedule()
        result = generate_scheduled_response(str(schedule.uuid))

        self.assertEqual(result, {"status": "skipped", "reason": reason})
        mock_llm.assert_not_called()
        self.assertFalse(self.bot_messages().exists())
        self.assertFalse(AITask.objects.filter(owner=self.bot_user).exists())
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)

    def test_creator_who_left_ends_the_schedule(self, mock_llm):
        self.leave(self.creator)
        self._assert_refused_and_deactivated(mock_llm, "not_a_member")

    def test_deactivated_creator_ends_the_schedule(self, mock_llm):
        self.deactivate(self.creator)
        self._assert_refused_and_deactivated(mock_llm, "user_inactive")

    def test_creator_who_lost_the_bot_ends_the_schedule(self, mock_llm):
        self.revoke_bot(self.creator)
        self._assert_refused_and_deactivated(mock_llm, "bot_not_accessible")


@patch("workspace.ai.harness.model.call_llm")
class AgentGoalCheckAccessTests(BotAccessFixture):
    def _goal(self):
        return AgentGoal.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.creator,
            title="Watch my inbox",
            goal="Tell the group about important mail.",
            next_check_at=timezone.now() - timedelta(minutes=1),
        )

    def _assert_refused_and_abandoned(self, mock_llm, reason):
        from workspace.ai.tasks.agent_goals import run_agent_goal_check

        mock_llm.return_value = _llm_reply("Here is the creator's private mail.")
        goal = self._goal()
        result = run_agent_goal_check(str(goal.uuid))

        self.assertEqual(result, {"status": "skipped", "reason": reason})
        mock_llm.assert_not_called()
        self.assertFalse(self.bot_messages().exists())
        self.assertFalse(AITask.objects.filter(owner=self.bot_user).exists())
        goal.refresh_from_db()
        self.assertEqual(goal.status, AgentGoal.Status.ABANDONED)
        self.assertIsNotNone(goal.closed_at)
        self.assertTrue(goal.outcome)

    def test_creator_who_left_abandons_the_goal(self, mock_llm):
        self.leave(self.creator)
        self._assert_refused_and_abandoned(mock_llm, "not_a_member")

    def test_deactivated_creator_abandons_the_goal(self, mock_llm):
        self.deactivate(self.creator)
        self._assert_refused_and_abandoned(mock_llm, "user_inactive")

    def test_creator_who_lost_the_bot_abandons_the_goal(self, mock_llm):
        self.revoke_bot(self.creator)
        self._assert_refused_and_abandoned(mock_llm, "bot_not_accessible")


@patch("workspace.ai.harness.model.call_llm")
class ChatResponseAccessTests(BotAccessFixture):
    def _respond_to(self, author, mock_llm):
        from workspace.ai.tasks.chat import generate_chat_response

        mock_llm.return_value = _llm_reply("Here is your private mail.")

        message = Message.objects.create(
            conversation=self.conversation, author=author, body="@privbot my mail?"
        )
        return generate_chat_response(
            str(self.conversation.pk), str(message.uuid), self.bot_user.id
        )

    def test_member_without_bot_access_gets_no_run(self, mock_llm):
        result = self._respond_to(self.other, mock_llm)

        self.assertEqual(result, {"status": "skipped", "reason": "bot_not_accessible"})
        mock_llm.assert_not_called()
        self.assertFalse(self.bot_messages().exists())
        self.assertFalse(AITask.objects.filter(owner=self.bot_user).exists())

    def test_access_revoked_after_the_conversation_was_set_up(self, mock_llm):
        self.revoke_bot(self.creator)

        result = self._respond_to(self.creator, mock_llm)

        self.assertEqual(result, {"status": "skipped", "reason": "bot_not_accessible"})
        mock_llm.assert_not_called()
        self.assertFalse(self.bot_messages().exists())
