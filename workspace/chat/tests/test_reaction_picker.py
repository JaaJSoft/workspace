from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    Reaction,
)
from workspace.chat.services.reactions import DEFAULT_QUICK_REACTIONS
from workspace.chat.ui.templatetags.chat_tags import render_reaction_picker

User = get_user_model()


class ReactionPickerTagTests(TestCase):
    """The hover overlay quick-reaction picker must flag emojis the current
    user already reacted with so the UI can show them as selected.
    """

    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", email="a@test.com", password="pw"
        )
        self.bob = User.objects.create_user(
            username="bob", email="b@test.com", password="pw"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            created_by=self.alice,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.alice)
        ConversationMember.objects.create(conversation=self.conv, user=self.bob)
        self.message = Message.objects.create(
            conversation=self.conv,
            author=self.alice,
            body="hello",
        )

    def _picker(self, message, user):
        ctx = render_reaction_picker(message, user, DEFAULT_QUICK_REACTIONS)
        return {r["emoji"]: r["has_mine"] for r in ctx["quick_reactions"]}

    def test_emoji_reacted_by_current_user_is_marked_mine(self):
        emoji = DEFAULT_QUICK_REACTIONS[0]
        Reaction.objects.create(message=self.message, user=self.alice, emoji=emoji)
        flags = self._picker(self.message, self.alice)
        self.assertTrue(flags[emoji])
        # Every other quick emoji stays unselected.
        for other in DEFAULT_QUICK_REACTIONS[1:]:
            self.assertFalse(flags[other])

    def test_reaction_by_another_user_is_not_marked_mine(self):
        emoji = DEFAULT_QUICK_REACTIONS[0]
        Reaction.objects.create(message=self.message, user=self.bob, emoji=emoji)
        flags = self._picker(self.message, self.alice)
        self.assertFalse(flags[emoji])

    def test_no_reactions_marks_nothing(self):
        flags = self._picker(self.message, self.alice)
        self.assertTrue(all(v is False for v in flags.values()))
        self.assertEqual(set(flags), set(DEFAULT_QUICK_REACTIONS))
