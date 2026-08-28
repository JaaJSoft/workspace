"""Tests for the chat page load (`chat_view`) and the state it hands to Alpine.

The sidebar partials render names and initials, but the page embeds the whole
conversation list as JSON: bot detection, mention autocomplete, the DM partner
and member management all read `conv.members` client-side. Nothing else pins
that payload down, so trimming what the refresh endpoints fetch must not trim
this.
"""

import json
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.models import Conversation, ConversationMember

from .test_chat import ChatTestMixin

User = get_user_model()

EMBEDDED = re.compile(
    r'<script id="conversations-data" type="application/json">(.*?)</script>',
    re.DOTALL,
)


class ChatPageMemberPayloadTests(ChatTestMixin, TestCase):
    URL = "/chat"

    def _embedded_conversations(self):
        html = self.client.get(self.URL).content.decode()
        match = EMBEDDED.search(html)
        self.assertIsNotNone(match, "the chat page must embed conversations-data")
        return json.loads(match.group(1))

    def test_every_member_of_a_crowded_group_reaches_the_client(self):
        crowd = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Crowded",
            created_by=self.creator,
        )
        ConversationMember.objects.create(conversation=crowd, user=self.creator)
        expected = {self.creator.id}
        for i in range(8):
            user = User.objects.create_user(username=f"crowd-{i}", password="pass")
            ConversationMember.objects.create(conversation=crowd, user=user)
            expected.add(user.id)

        self.client.force_login(self.creator)
        payload = self._embedded_conversations()

        conv = next(c for c in payload if c["uuid"] == str(crowd.uuid))
        self.assertEqual({m["user"]["id"] for m in conv["members"]}, expected)
        self.assertEqual(conv["member_count"], len(expected))

    def test_a_member_who_left_is_kept_out_of_the_payload(self):
        self.client.force_login(self.creator)
        payload = self._embedded_conversations()
        group = next(c for c in payload if c["uuid"] == str(self.group.uuid))
        self.assertIn(self.member.id, {m["user"]["id"] for m in group["members"]})

        ConversationMember.objects.filter(
            conversation=self.group, user=self.member
        ).update(left_at="2026-01-01T00:00:00Z")

        payload = self._embedded_conversations()
        group = next(c for c in payload if c["uuid"] == str(self.group.uuid))
        self.assertNotIn(self.member.id, {m["user"]["id"] for m in group["members"]})
