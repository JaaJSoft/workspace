from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.ai.models import AgentGoal, AITask, BotProfile, ScheduledMessage
from workspace.ai.services.bot_access import run_refusal
from workspace.chat.models import Conversation, ConversationMember, Message

from .agents import reply, scripted
from .chat_tasks import queue_chat_task

User = get_user_model()


class BotAccessFixture(TestCase):
    """A direct conversation with a private bot the creator reaches through a group."""

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
        self.conversation = self._conversation(
            Conversation.Kind.DM, self.creator, self.bot_user
        )

    def _conversation(self, kind, *members):
        conversation = Conversation.objects.create(
            kind=kind, created_by=members[0], title="g"
        )
        for member in members:
            ConversationMember.objects.create(conversation=conversation, user=member)
        return conversation

    def make_group(self):
        """Swap the conversation for a group of the creator, another person and the bot."""
        self.conversation = self._conversation(
            Conversation.Kind.GROUP, self.creator, self.other, self.bot_user
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
            "not_a_member",
        )
        self.revoke_bot(self.creator)
        self.assertEqual(
            run_refusal(self.bot_profile, self.creator, self.conversation.pk),
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

    def test_a_group_is_refused_even_to_a_creator_allowed_to_use_the_bot(self):
        self.make_group()
        self.assertEqual(
            run_refusal(self.bot_profile, self.creator, self.conversation.pk),
            "not_a_direct_conversation",
        )


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

    def _assert_refused_and_deactivated(self, reason):
        from workspace.ai.tasks.scheduled import generate_scheduled_response

        schedule = self._schedule()
        with scripted(reply("Here is the creator's private mail.")) as script:
            result = generate_scheduled_response(
                str(schedule.uuid), schedule.next_run_at.isoformat()
            )

        self.assertEqual(result, {"status": "skipped", "reason": reason})
        self.assertEqual(script.requests, [])
        self.assertFalse(self.bot_messages().exists())
        self.assertFalse(AITask.objects.filter(owner=self.bot_user).exists())
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)

    def test_creator_who_left_ends_the_schedule(self):
        self.leave(self.creator)
        self._assert_refused_and_deactivated("not_a_member")

    def test_deactivated_creator_ends_the_schedule(self):
        self.deactivate(self.creator)
        self._assert_refused_and_deactivated("user_inactive")

    def test_creator_who_lost_the_bot_ends_the_schedule(self):
        self.revoke_bot(self.creator)
        self._assert_refused_and_deactivated("bot_not_accessible")

    def test_a_schedule_in_a_group_is_ended(self):
        self.make_group()
        self._assert_refused_and_deactivated("not_a_direct_conversation")


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

    def _assert_refused_and_abandoned(self, reason):
        from workspace.ai.tasks.agent_goals import run_agent_goal_check

        goal = self._goal()
        with scripted(reply("Here is the creator's private mail.")) as script:
            result = run_agent_goal_check(
                str(goal.uuid), goal.next_check_at.isoformat()
            )

        self.assertEqual(result, {"status": "skipped", "reason": reason})
        self.assertEqual(script.requests, [])
        self.assertFalse(self.bot_messages().exists())
        self.assertFalse(AITask.objects.filter(owner=self.bot_user).exists())
        goal.refresh_from_db()
        self.assertEqual(goal.status, AgentGoal.Status.ABANDONED)
        self.assertIsNotNone(goal.closed_at)
        self.assertTrue(goal.outcome)

    def test_creator_who_left_abandons_the_goal(self):
        self.leave(self.creator)
        self._assert_refused_and_abandoned("not_a_member")

    def test_deactivated_creator_abandons_the_goal(self):
        self.deactivate(self.creator)
        self._assert_refused_and_abandoned("user_inactive")

    def test_creator_who_lost_the_bot_abandons_the_goal(self):
        self.revoke_bot(self.creator)
        self._assert_refused_and_abandoned("bot_not_accessible")

    def test_a_goal_in_a_group_is_abandoned(self):
        self.make_group()
        self._assert_refused_and_abandoned("not_a_direct_conversation")


class ChatResponseAccessTests(BotAccessFixture):
    def _respond_to(self, author):
        """The task's result, and the requests the model was sent."""
        from workspace.ai.tasks.chat import generate_chat_response

        message = Message.objects.create(
            conversation=self.conversation, author=author, body="@privbot my mail?"
        )
        with scripted(reply("Here is your private mail.")) as script:
            result = generate_chat_response(
                queue_chat_task(self.conversation, message, self.bot_user)
            )
        return result, script.requests

    def test_a_group_gets_no_run_even_for_a_member_allowed_to_use_the_bot(self):
        """In a group the tool results of the creator's mail would be shown
        to every other member."""
        self.make_group()

        result, requests = self._respond_to(self.creator)

        self.assertEqual(
            result, {"status": "skipped", "reason": "not_a_direct_conversation"}
        )
        self.assertEqual(requests, [])
        self.assertFalse(self.bot_messages().exists())
        # Closed, or the chat would keep showing the refused reply as under way.
        self.assertEqual(
            list(
                AITask.objects.filter(owner=self.bot_user).values_list(
                    "status", flat=True
                )
            ),
            [AITask.Status.FAILED],
        )

    def test_access_revoked_after_the_conversation_was_set_up(self):
        self.revoke_bot(self.creator)

        result, requests = self._respond_to(self.creator)

        self.assertEqual(result, {"status": "skipped", "reason": "bot_not_accessible"})
        self.assertEqual(requests, [])
        self.assertFalse(self.bot_messages().exists())
