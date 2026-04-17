"""Tests for the chat UI `conversation_list_view` partial endpoint.

Covers the HTML partial returned for the chat sidebar (Alpine AJAX refresh),
including the `?q=` search filter.
"""
from django.test import TestCase
from django.urls import reverse

from workspace.chat.models import Conversation, ConversationMember

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
