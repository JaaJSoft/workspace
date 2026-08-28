import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.ui.views import _build_conversation_context

User = get_user_model()


class ChatRoomViewTests(TestCase):
    def setUp(self):
        self.member = User.objects.create_user(username="member", password="pw")
        self.outsider = User.objects.create_user(username="outsider", password="pw")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.member
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.member
        )

    def _url(self):
        return reverse(
            "chat_ui:room", kwargs={"conversation_uuid": self.conversation.uuid}
        )

    def test_member_gets_room_page(self):
        self.client.force_login(self.member)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "chat/ui/room.html")
        self.assertEqual(
            str(resp.context["conversation_uuid"]), str(self.conversation.uuid)
        )

    def test_non_member_is_forbidden(self):
        self.client.force_login(self.outsider)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp["Location"])

    def test_room_conversation_data_is_embedded(self):
        """Regression: the room page must embed a fully-shaped conversation object
        so the reused conversation_pane.html renders the real name instead of
        'Group'. Pins that room-conversation-data carries title, kind, uuid,
        members with user data, and is_bot_conversation."""
        other = User.objects.create_user(username="other_member", password="pw2")
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Team Chat",
            created_by=self.member,
        )
        ConversationMember.objects.create(conversation=conv, user=self.member)
        ConversationMember.objects.create(conversation=conv, user=other)

        self.client.force_login(self.member)
        url = reverse("chat_ui:room", kwargs={"conversation_uuid": conv.uuid})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode()
        self.assertIn(
            '<script id="room-conversation-data" type="application/json">',
            content,
            "room-conversation-data script tag not found in room page",
        )

        m = re.search(
            r'<script id="room-conversation-data" type="application/json">(.*?)</script>',
            content,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "room-conversation-data block not parseable")
        data = json.loads(m.group(1))

        self.assertEqual(data["title"], "Team Chat")
        self.assertEqual(data["kind"], "group")
        self.assertEqual(str(data["uuid"]), str(conv.uuid))
        self.assertIn("is_bot_conversation", data)
        members = data.get("members", [])
        self.assertTrue(len(members) > 0, "members array is empty")
        usernames = {mbr["user"]["username"] for mbr in members}
        self.assertIn("other_member", usernames)

    def test_room_page_loads_the_message_shell(self):
        """Regression: the room reuses conversation_pane.html, whose messages are
        server-rendered as <chat-message-group> shells with slotted children. The
        bubble chrome - avatar column, bubble colours, author line, footer - is
        built by message_shell.js; without it the element never upgrades and every
        message renders as bare text."""
        self.client.force_login(self.member)
        resp = self.client.get(self._url())
        self.assertIn("chat/ui/js/message_shell.js", resp.content.decode())


class RoomTitleMatchesTheSidebarTests(TestCase):
    """The room heading and the sidebar row must name a conversation alike.

    They are two renderings of the same label, and they used to derive it from
    different things - the room from its members, the sidebar from the stored
    title - so the same conversation read differently depending on the tab.
    """

    def setUp(self):
        self.alice = User.objects.create_user(
            username="alice", password="pw", first_name="Alice", last_name="Ng"
        )
        self.bob = User.objects.create_user(username="bob", password="pw")
        self.carol = User.objects.create_user(username="carol", password="pw")
        self.client.force_login(self.alice)

    def _conversation(self, kind, members, title=""):
        conversation = Conversation.objects.create(
            kind=kind, title=title, created_by=self.alice
        )
        for user in members:
            ConversationMember.objects.create(conversation=conversation, user=user)
        return conversation

    def _room_title(self, conversation):
        url = reverse("chat_ui:room", kwargs={"conversation_uuid": conversation.uuid})
        return self.client.get(url).context["conversation_title"]

    def _sidebar_name(self, conversation):
        convs = _build_conversation_context(self.alice)
        return next(c for c in convs if c.pk == conversation.pk).display_name

    def test_a_titled_group_reads_the_same_in_both(self):
        group = self._conversation(
            Conversation.Kind.GROUP,
            [self.alice, self.bob, self.carol],
            title="Product Launch",
        )

        self.assertEqual(self._room_title(group), "Product Launch")
        self.assertEqual(self._room_title(group), self._sidebar_name(group))

    def test_a_direct_message_reads_the_same_in_both(self):
        dm = self._conversation(Conversation.Kind.DM, [self.alice, self.bob])

        self.assertEqual(self._room_title(dm), "bob")
        self.assertEqual(self._room_title(dm), self._sidebar_name(dm))

    def test_a_group_that_slipped_through_without_a_name_reads_the_same(self):
        """Groups are named on creation and backfilled, so this is the guard.

        A row built straight through the ORM still has to label the same way in
        both places rather than falling back to two different things.
        """
        group = self._conversation(
            Conversation.Kind.GROUP, [self.alice, self.bob, self.carol]
        )

        self.assertEqual(self._room_title(group), self._sidebar_name(group))
