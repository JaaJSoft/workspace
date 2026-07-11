"""Tests for the chat UI `conversation_items_view` partial endpoint.

Covers the per-conversation sidebar rows returned for targeted alpine-ajax
swaps (`/chat/conversations/items?uuids=...`) after a message is sent or
received.
"""

from django.test import TestCase

from workspace.chat.models import Conversation, ConversationMember, PinnedConversation

from .test_chat import ChatTestMixin


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
