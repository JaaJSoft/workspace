"""Tests for the chat UI `conversation_list_view` partial endpoint.

Covers the HTML partial returned for the chat sidebar (Alpine AJAX refresh),
including the `?q=` search filter.
"""
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import Conversation, ConversationMember, PinnedConversation
from .test_chat import ChatTestMixin


class ConversationListViewPartialTests(ChatTestMixin, TestCase):
    URL = '/chat/conversations'

    def test_unauthenticated_redirects_to_login(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)

    def test_returns_html_partial_with_conversation_list_root(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="conversation-list"')
        self.assertContains(resp, 'Test Group')

    def test_search_filters_group_conversation_by_title(self):
        other_group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='Project Phoenix',
            created_by=self.creator,
        )
        ConversationMember.objects.create(conversation=other_group, user=self.creator)

        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {'q': 'phoenix'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Project Phoenix')
        self.assertNotContains(resp, 'Test Group')

    def test_search_filters_dm_by_other_member_name(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {'q': 'member'})
        self.assertEqual(resp.status_code, 200)
        # DM with member should be visible (display_name = "member")
        self.assertContains(resp, 'id="conversation-list"')
        # Group should be filtered out (title is "Test Group", no "member")
        self.assertNotContains(resp, 'Test Group')

    def test_search_is_case_insensitive(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {'q': 'TEST GROUP'})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Test Group')

    def test_blank_search_returns_all_conversations(self):
        self.client.force_login(self.creator)
        resp = self.client.get(self.URL, {'q': '   '})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Test Group')

    def test_dm_and_group_appear_in_single_merged_list(self):
        """DM and Group are rendered together, sorted by updated_at desc."""
        # Group older, DM more recent.
        now = timezone.now()
        Conversation.objects.filter(pk=self.group.pk).update(updated_at=now - timedelta(hours=2))
        Conversation.objects.filter(pk=self.dm.pk).update(updated_at=now - timedelta(hours=1))

        self.client.force_login(self.creator)
        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()

        # Both should be in the merged list.
        self.assertIn(str(self.group.uuid), body)
        self.assertIn(str(self.dm.uuid), body)

        # DM (more recent) should appear before the group in document order.
        dm_pos = body.find(str(self.dm.uuid))
        group_pos = body.find(str(self.group.uuid))
        self.assertNotEqual(dm_pos, -1, 'DM uuid should appear in the rendered HTML')
        self.assertNotEqual(group_pos, -1, 'Group uuid should appear in the rendered HTML')
        self.assertLess(dm_pos, group_pos,
                        'More recently updated DM should be rendered before the older group')

    def test_no_section_headers_for_dm_or_group(self):
        """The DMs/Groups section headers no longer exist; Pinned header still does."""
        # Pin the group so the Pinned section renders
        PinnedConversation.objects.create(
            owner=self.creator, conversation=self.group, position=0,
        )

        self.client.force_login(self.creator)
        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        # Old section headers must be gone
        self.assertNotContains(resp, 'Direct Messages')
        self.assertNotContains(resp, '>Groups<')  # avoid matching avatar group containers
        # Pinned header must remain
        self.assertContains(resp, 'Pinned')

    def test_pinned_section_remains_separate(self):
        """A pinned conversation is rendered before non-pinned ones, regardless of updated_at."""
        # Group is pinned but older; DM is unpinned but more recent.
        now = timezone.now()
        Conversation.objects.filter(pk=self.group.pk).update(updated_at=now - timedelta(days=10))
        Conversation.objects.filter(pk=self.dm.pk).update(updated_at=now)
        PinnedConversation.objects.create(
            owner=self.creator, conversation=self.group, position=0,
        )

        self.client.force_login(self.creator)
        resp = self.client.get(self.URL)

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()

        group_pos = body.find(str(self.group.uuid))
        dm_pos = body.find(str(self.dm.uuid))
        self.assertNotEqual(group_pos, -1, 'Group uuid should appear in the rendered HTML')
        self.assertNotEqual(dm_pos, -1, 'DM uuid should appear in the rendered HTML')
        self.assertLess(group_pos, dm_pos,
                        'Pinned group must come before the more recent unpinned DM')
