"""Tests for the chat UI `conversation_items_view` partial endpoint.

Covers the per-conversation sidebar rows returned for targeted alpine-ajax
swaps (`/chat/conversations/items?uuids=...`) after a message is sent or
received.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.chat.models import Conversation, ConversationMember, PinnedConversation
from workspace.common.tests.rows import count_rows

from .test_chat import ChatTestMixin

User = get_user_model()


class ConversationItemsViewPartialTests(ChatTestMixin, TestCase):
    URL = "/chat/conversations/items"

    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.get(self.URL, {"uuids": str(self.group.uuid)})
        self.assertEqual(resp.status_code, 302)

    def test_returns_only_requested_conversation_row(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {"uuids": str(self.group.uuid)})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'id="conv-item-{self.group.uuid}"')
        self.assertNotContains(resp, f'id="conv-item-{self.dm.uuid}"')

    def test_returns_multiple_rows_for_multiple_uuids(self):
        self.client.force_login(self.creator)
        resp = self.client.get(
            self.URL, {"uuids": [str(self.group.uuid), str(self.dm.uuid)]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'id="conv-item-{self.group.uuid}"')
        self.assertContains(resp, f'id="conv-item-{self.dm.uuid}"')

    def test_missing_uuids_param_returns_400(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 400)

    def test_blank_uuids_param_returns_400(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {"uuids": ""})
        self.assertEqual(resp.status_code, 400)

    def test_malformed_uuid_returns_400(self):
        self.client.force_login(self.creator)
        resp = self.client.get(
            self.URL, {"uuids": [str(self.group.uuid), "not-a-uuid"]}
        )
        self.assertEqual(resp.status_code, 400)

    def test_non_member_conversation_is_silently_dropped(self):
        other = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Outsider Group",
            created_by=self.outsider,
        )
        ConversationMember.objects.create(conversation=other, user=self.outsider)

        self.client.force_login(self.creator)
        resp = self.client.get(
            self.URL, {"uuids": [str(self.group.uuid), str(other.uuid)]}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'id="conv-item-{self.group.uuid}"')
        self.assertNotContains(resp, f'id="conv-item-{other.uuid}"')
        self.assertNotContains(resp, "Outsider Group")

    def test_pinned_conversation_row_keeps_pinned_markup(self):
        PinnedConversation.objects.create(
            owner=self.creator,
            conversation=self.group,
            position=0,
        )

        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {"uuids": str(self.group.uuid)})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'id="conv-item-{self.group.uuid}"')
        # Pinned rows must keep the drag-reorder markup so the swapped row
        # behaves exactly like the one rendered by the full list.
        self.assertContains(resp, 'draggable="true"')

    def test_unpinned_conversation_row_has_no_pinned_markup(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {"uuids": str(self.group.uuid)})
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'draggable="true"')


class ConversationItemsRowVolumeTests(ChatTestMixin, TestCase):
    """The swap fired after every message must not read the whole member list.

    A group row is labelled from its stored title, so a conversation with five
    members and one with fifty-five have to cost the same. The query count is
    already constant, which is precisely why it catches nothing here.
    """

    URL = "/chat/conversations/items"

    def _add_members(self, count, prefix):
        for i in range(count):
            user = User.objects.create_user(username=f"{prefix}{i}", password="pass")
            ConversationMember.objects.create(conversation=self.group, user=user)

    def test_row_volume_does_not_scale_with_members_per_conversation(self):
        self.client.force_login(self.creator)
        self._add_members(5, "seed-")

        with count_rows(ConversationMember) as baseline:
            self.client.get(self.URL, {"uuids": str(self.group.uuid)})

        self._add_members(50, "bulk-")
        with count_rows(ConversationMember) as after:
            resp = self.client.get(self.URL, {"uuids": str(self.group.uuid)})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            after.count,
            baseline.count,
            msg=(
                "ConversationMember rows must not scale with members per "
                f"conversation - baseline={baseline.count}, "
                f"after adding 50 members={after.count}"
            ),
        )
