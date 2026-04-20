from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)
from workspace.common.cache import invalidate_tags

User = get_user_model()


class AttachmentDownloadTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='pass')
        self.member = User.objects.create_user(username='member', password='pass')
        self.outsider = User.objects.create_user(username='outsider', password='pass')
        self.left_user = User.objects.create_user(username='left', password='pass')

        self.group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title='Group',
            created_by=self.owner,
        )
        ConversationMember.objects.create(conversation=self.group, user=self.owner)
        ConversationMember.objects.create(conversation=self.group, user=self.member)
        ConversationMember.objects.create(
            conversation=self.group, user=self.left_user, left_at=timezone.now(),
        )

        self.message = Message.objects.create(
            conversation=self.group, author=self.owner, body='hi',
        )
        self.attachment = MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile('doc.pdf', b'pdf-bytes', content_type='application/pdf'),
            original_name='doc.pdf',
            mime_type='application/pdf',
            size=9,
        )

    def url(self, uuid):
        return f'/api/v1/chat/attachments/{uuid}'

    def _consume(self, response):
        # FileResponse is a streaming response — consume it so the file handle
        # is released before the TestCase tears down the temporary storage.
        try:
            b''.join(response.streaming_content)
        except AttributeError:
            pass

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url(self.attachment.uuid))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_can_download(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url(self.attachment.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('filename="doc.pdf"', resp['Content-Disposition'])
        self.assertEqual(resp['Cache-Control'], 'private, max-age=604800, immutable')
        self._consume(resp)

    def test_member_can_download(self):
        self.client.force_authenticate(self.member)
        resp = self.client.get(self.url(self.attachment.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self._consume(resp)

    def test_outsider_rejected(self):
        # Both "not found" and "not member" return 404 to avoid leaking existence
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.attachment.uuid))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_user_who_left_is_rejected(self):
        self.client.force_authenticate(self.left_user)
        resp = self.client.get(self.url(self.attachment.uuid))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_unknown_uuid_returns_404(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url('00000000-0000-0000-0000-000000000000'))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_filename_is_sanitized(self):
        evil = MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile('x.pdf', b'x', content_type='application/pdf'),
            original_name='na"me\nwith\rbad.pdf',
            mime_type='application/pdf',
            size=1,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(self.url(evil.uuid))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        disposition = resp['Content-Disposition']
        self.assertNotIn('\n', disposition)
        self.assertNotIn('\r', disposition)
        self.assertIn('na\\"mewithbad.pdf', disposition)
        self._consume(resp)

    def _warm(self, user):
        """Trigger PresenceMiddleware's DB sync, then invalidate the attachment
        tag so the next request re-exercises the view's DB path from a cold state."""
        self.client.force_authenticate(user)
        self._consume(self.client.get(self.url(self.attachment.uuid)))
        invalidate_tags(f'att:{self.attachment.uuid}')

    def test_cold_query_count_is_two(self):
        """First download: attachment+join-message (1) + membership (1)."""
        self._warm(self.owner)
        with self.assertNumQueries(2):
            resp = self.client.get(self.url(self.attachment.uuid))
            self._consume(resp)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_second_request_hits_permission_cache(self):
        self._warm(self.owner)
        self._consume(self.client.get(self.url(self.attachment.uuid)))  # populate cache
        with self.assertNumQueries(0):
            resp = self.client.get(self.url(self.attachment.uuid))
            self._consume(resp)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_cache_is_per_user(self):
        """A user's cached meta must never let another user download."""
        self.client.force_authenticate(self.owner)
        self._consume(self.client.get(self.url(self.attachment.uuid)))

        self.client.force_authenticate(self.outsider)
        resp = self.client.get(self.url(self.attachment.uuid))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_missing_file_invalidates_cache_and_returns_404(self):
        """If the underlying file has disappeared, the cache is flushed and the
        next request re-validates from the DB (which still sees the metadata)."""
        from django.core.cache import cache
        from django.core.files.storage import default_storage

        self.client.force_authenticate(self.owner)
        self._consume(self.client.get(self.url(self.attachment.uuid)))
        default_storage.delete(self.attachment.file.name)

        resp = self.client.get(self.url(self.attachment.uuid))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def tearDown(self):
        from django.core.cache import cache
        cache.clear()
