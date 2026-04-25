"""Tests for `message_readers_view` — the read-receipt popover partial."""
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import ConversationMember, Message
from .test_chat import ChatTestMixin


class MessageReadersViewTests(ChatTestMixin, TestCase):
    def _url(self, conv_uuid, msg_uuid):
        return f'/chat/{conv_uuid}/messages/{msg_uuid}/readers'

    def _make_message(self, conv, author):
        return Message.objects.create(
            conversation=conv, author=author, body='hello',
        )

    def test_unauthenticated_redirects_to_login(self):
        msg = self._make_message(self.group, self.creator)
        resp = self.client.get(self._url(self.group.uuid, msg.uuid))
        self.assertEqual(resp.status_code, 302)

    def test_non_member_is_forbidden(self):
        msg = self._make_message(self.group, self.creator)
        self.client.force_login(self.outsider)
        resp = self.client.get(self._url(self.group.uuid, msg.uuid))
        self.assertEqual(resp.status_code, 403)

    def test_renders_popover_with_target_id_wrapper(self):
        msg = self._make_message(self.group, self.creator)
        self.client.force_login(self.creator)
        resp = self.client.get(self._url(self.group.uuid, msg.uuid))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, f'id="readers-popover-{msg.uuid}"')

    def test_lists_readers_in_readers_section(self):
        msg = self._make_message(self.group, self.creator)
        cm = ConversationMember.objects.get(conversation=self.group, user=self.member)
        cm.last_read_at = timezone.now()
        cm.save(update_fields=['last_read_at'])

        self.client.force_login(self.creator)
        resp = self.client.get(self._url(self.group.uuid, msg.uuid))
        self.assertContains(resp, 'Read by')
        self.assertContains(resp, self.member.username)
        self.assertNotContains(resp, 'Not yet')

    def test_lists_not_yet_read_members(self):
        msg = self._make_message(self.group, self.creator)
        self.client.force_login(self.creator)
        resp = self.client.get(self._url(self.group.uuid, msg.uuid))
        self.assertContains(resp, 'Not yet')
        self.assertContains(resp, self.member.username)
