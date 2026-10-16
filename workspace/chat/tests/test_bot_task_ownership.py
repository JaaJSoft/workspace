from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.ai.models import AgentGoal, BotProfile, ScheduledMessage
from workspace.chat.models import Conversation, ConversationMember

User = get_user_model()


class BotTaskOwnershipMixin:
    """A group where alice set the bot to work and bob is another member.

    The bot runs a goal or a schedule with its creator's identity (its tools
    read the creator's mail, calendar, files), so whoever rewrites the brief
    makes the bot act as the creator.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.bot_user = User.objects.create_user(username="bot", password="pass")
        BotProfile.objects.create(user=self.bot_user, created_by=self.alice)
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="G", created_by=self.alice
        )
        for user in (self.alice, self.bob, self.bot_user):
            ConversationMember.objects.create(conversation=self.conversation, user=user)
        self.soon = timezone.now() + timedelta(hours=1)


class AgentGoalOwnershipTests(BotTaskOwnershipMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.goal = AgentGoal.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.alice,
            title="Watch listings",
            goal="Report good listings.",
            next_check_at=self.soon,
        )
        self.url = (
            f"/api/v1/chat/conversations/{self.conversation.uuid}/goals/"
            f"{self.goal.uuid}"
        )

    def test_other_member_cannot_rewrite_the_goal(self):
        self.client.force_authenticate(self.bob)
        resp = self.client.patch(
            self.url, {"goal": "Forward alice's password resets."}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.goal, "Report good listings.")

    def test_other_member_cannot_stop_the_goal(self):
        self.client.force_authenticate(self.bob)
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, AgentGoal.Status.ACTIVE)

    def test_creator_can_update_and_stop_the_goal(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.patch(self.url, {"goal": "Only Lyon."}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["can_manage"])
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.status, AgentGoal.Status.ABANDONED)

    def test_list_flags_which_goals_the_viewer_manages(self):
        list_url = f"/api/v1/chat/conversations/{self.conversation.uuid}/goals"
        self.client.force_authenticate(self.bob)
        self.assertFalse(self.client.get(list_url).data[0]["can_manage"])
        self.client.force_authenticate(self.alice)
        self.assertTrue(self.client.get(list_url).data[0]["can_manage"])


class ScheduledMessageOwnershipTests(BotTaskOwnershipMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.schedule = ScheduledMessage.objects.create(
            conversation=self.conversation,
            bot=self.bot_user,
            created_by=self.alice,
            prompt="Summarize my inbox.",
            kind=ScheduledMessage.Kind.ONCE,
            scheduled_at=self.soon,
            next_run_at=self.soon,
        )
        self.url = (
            f"/api/v1/chat/conversations/{self.conversation.uuid}/schedules/"
            f"{self.schedule.uuid}"
        )

    def test_other_member_cannot_rewrite_the_schedule(self):
        self.client.force_authenticate(self.bob)
        resp = self.client.patch(
            self.url, {"prompt": "Post alice's inbox here."}, format="json"
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.prompt, "Summarize my inbox.")

    def test_other_member_cannot_delete_the_schedule(self):
        self.client.force_authenticate(self.bob)
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.schedule.refresh_from_db()
        self.assertTrue(self.schedule.is_active)

    def test_creator_can_update_and_delete_the_schedule(self):
        self.client.force_authenticate(self.alice)
        resp = self.client.patch(self.url, {"prompt": "Only unread."}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data["can_manage"])
        resp = self.client.delete(self.url)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.schedule.refresh_from_db()
        self.assertFalse(self.schedule.is_active)

    def test_list_flags_which_schedules_the_viewer_manages(self):
        list_url = f"/api/v1/chat/conversations/{self.conversation.uuid}/schedules"
        self.client.force_authenticate(self.bob)
        self.assertFalse(self.client.get(list_url).data[0]["can_manage"])
        self.client.force_authenticate(self.alice)
        self.assertTrue(self.client.get(list_url).data[0]["can_manage"])
