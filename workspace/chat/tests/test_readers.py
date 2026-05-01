from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.chat.models import ConversationMember, Message

from .test_chat import ChatTestMixin


class MessageReadersTests(ChatTestMixin, APITestCase):
    """Tests for GET /api/v1/chat/conversations/<id>/messages/<id>/readers"""

    def _url(self, conv_uuid, msg_uuid):
        return f'/api/v1/chat/conversations/{conv_uuid}/messages/{msg_uuid}/readers'

    def test_readers_empty_when_nobody_read(self):
        self.client.force_authenticate(self.creator)
        msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='Hello',
        )
        resp = self.client.get(self._url(self.group.uuid, msg.uuid))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['read_count'], 0)
        self.assertEqual(len(resp.data['not_read']), 1)

    def test_readers_after_mark_read(self):
        msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='Hello',
        )
        membership = ConversationMember.objects.get(
            conversation=self.group, user=self.member,
        )
        membership.last_read_at = timezone.now()
        membership.save(update_fields=['last_read_at'])

        self.client.force_authenticate(self.creator)
        resp = self.client.get(self._url(self.group.uuid, msg.uuid))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['read_count'], 1)
        self.assertEqual(resp.data['readers'][0]['username'], 'member')

    def test_author_excluded_from_readers(self):
        self.client.force_authenticate(self.creator)
        msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='Hello',
        )
        resp = self.client.get(self._url(self.group.uuid, msg.uuid))
        user_ids = [r['user_id'] for r in resp.data['readers']] + \
                   [r['user_id'] for r in resp.data['not_read']]
        self.assertNotIn(self.creator.id, user_ids)

    def test_non_member_forbidden(self):
        self.client.force_authenticate(self.outsider)
        msg = Message.objects.create(
            conversation=self.group, author=self.creator, body='Hello',
        )
        resp = self.client.get(self._url(self.group.uuid, msg.uuid))
        self.assertEqual(resp.status_code, 403)
