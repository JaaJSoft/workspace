from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.ai.models import BotProfile
from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services.conversations import (
    bot_may_run_in,
    can_join_group,
    is_bot_conversation,
)

User = get_user_model()


class BotGroupRulesTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bot = User.objects.create_user(username="bot")
        BotProfile.objects.create(user=self.bot, is_public=True)

    def _users(self, *users):
        return User.objects.filter(pk__in=[u.pk for u in users]).select_related(
            "bot_profile"
        )

    def test_people_may_join_a_group(self):
        bob = User.objects.create_user(username="bob", password="x")
        self.assertTrue(can_join_group(self._users(self.alice, bob)))

    def test_a_bot_may_not_join_a_group(self):
        self.assertFalse(can_join_group(self._users(self.alice, self.bot)))

    def test_a_bot_may_run_only_in_a_direct_conversation(self):
        dm = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.alice
        )
        group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="g", created_by=self.alice
        )
        self.assertTrue(bot_may_run_in(dm.pk))
        self.assertFalse(bot_may_run_in(group.pk))

    def test_a_bot_that_left_does_not_make_a_bot_conversation(self):
        group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="g", created_by=self.alice
        )
        ConversationMember.objects.create(conversation=group, user=self.alice)
        member = ConversationMember.objects.create(conversation=group, user=self.bot)
        self.assertTrue(is_bot_conversation(group.pk))

        member.left_at = timezone.now()
        member.save(update_fields=["left_at"])

        self.assertFalse(is_bot_conversation(group.pk))
