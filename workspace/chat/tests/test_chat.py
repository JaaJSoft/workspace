"""Common chat test mixin.

The individual test classes that used to live in this module have been split
into focused files (test_members, test_avatar, test_stats, test_search,
test_readers, test_providers, test_render). ``ChatTestMixin`` stays here
because several existing test files still import it from
``workspace.chat.tests.test_chat``.
"""

from django.contrib.auth import get_user_model

from workspace.chat.models import Conversation, ConversationMember

User = get_user_model()


class ChatTestMixin:
    """Common setup for chat tests."""

    def setUp(self):
        self.creator = User.objects.create_user(
            username="creator",
            email="creator@test.com",
            password="pass123",
        )
        self.member = User.objects.create_user(
            username="member",
            email="member@test.com",
            password="pass123",
        )
        self.outsider = User.objects.create_user(
            username="outsider",
            email="outsider@test.com",
            password="pass123",
        )
        self.extra_user = User.objects.create_user(
            username="extra",
            email="extra@test.com",
            password="pass123",
        )

        # Create a group conversation owned by creator, with member
        self.group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Test Group",
            created_by=self.creator,
        )
        ConversationMember.objects.create(
            conversation=self.group,
            user=self.creator,
        )
        ConversationMember.objects.create(
            conversation=self.group,
            user=self.member,
        )

        # Create a DM between creator and member
        self.dm = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.creator,
        )
        ConversationMember.objects.create(
            conversation=self.dm,
            user=self.creator,
        )
        ConversationMember.objects.create(
            conversation=self.dm,
            user=self.member,
        )
