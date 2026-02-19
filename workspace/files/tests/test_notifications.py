from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileComment, FileShare
from workspace.notifications.models import Notification

User = get_user_model()


class FileNotificationTestBase(APITestCase):
    """Common setup for file notification tests."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner', email='owner@example.com', password='pass123',
        )
        self.recipient = User.objects.create_user(
            username='recipient', email='recipient@example.com', password='pass123',
        )
        self.file = File.objects.create(
            owner=self.owner, name='doc.txt',
            node_type=File.NodeType.FILE, mime_type='text/plain',
        )
        self.file.content = ContentFile(b'Hello', name='doc.txt')
        self.file.size = 5
        self.file.save()
        self.client.force_authenticate(user=self.owner)

    def _notifs_for(self, user, origin='files'):
        return Notification.objects.filter(recipient=user, origin=origin)


class ShareNotificationTests(FileNotificationTestBase):
    """Tests for notifications triggered by sharing actions."""

    def test_share_creates_notification(self):
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.recipient.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        notifs = self._notifs_for(self.recipient)
        self.assertEqual(notifs.count(), 1)
        n = notifs.first()
        self.assertIn('shared', n.title)
        self.assertIn('doc.txt', n.title)
        self.assertEqual(n.url, f'/files/{self.file.uuid}')
        self.assertEqual(n.actor, self.owner)

    def test_share_duplicate_no_extra_notification(self):
        """Re-sharing with the same permission should not create a new notification."""
        self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.recipient.pk},
            format='json',
        )
        self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.recipient.pk},
            format='json',
        )
        # Only the initial share notification should exist
        self.assertEqual(self._notifs_for(self.recipient).count(), 1)

    def test_permission_update_creates_notification(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.owner,
            shared_with=self.recipient, permission='ro',
        )
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.recipient.pk, 'permission': 'rw'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notifs = self._notifs_for(self.recipient)
        self.assertEqual(notifs.count(), 1)
        self.assertIn('read & write', notifs.first().title)

    def test_permission_update_ro_label(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.owner,
            shared_with=self.recipient, permission='rw',
        )
        self.client.post(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.recipient.pk, 'permission': 'ro'},
            format='json',
        )
        notif = self._notifs_for(self.recipient).first()
        self.assertIn('read only', notif.title)

    def test_revoke_share_creates_notification(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.recipient,
        )
        resp = self.client.delete(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.recipient.pk},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notifs = self._notifs_for(self.recipient)
        self.assertEqual(notifs.count(), 1)
        n = notifs.first()
        self.assertIn('revoked', n.title)
        self.assertEqual(n.url, '')  # no url since access removed

    def test_revoke_nonexistent_share_no_notification(self):
        self.client.delete(
            f'/api/v1/files/{self.file.uuid}/share',
            {'shared_with': self.recipient.pk},
            format='json',
        )
        self.assertEqual(self._notifs_for(self.recipient).count(), 0)


class CommentNotificationTests(FileNotificationTestBase):
    """Tests for notifications triggered by comments."""

    def test_comment_notifies_owner(self):
        """When a shared user comments, the file owner gets notified."""
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.recipient,
        )
        self.client.force_authenticate(user=self.recipient)
        resp = self.client.post(
            f'/api/v1/files/{self.file.uuid}/comments',
            {'body': 'Nice file!'},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        notifs = self._notifs_for(self.owner)
        self.assertEqual(notifs.count(), 1)
        self.assertIn('commented', notifs.first().title)
        self.assertEqual(notifs.first().actor, self.recipient)

    def test_comment_by_owner_no_self_notification(self):
        """Owner commenting on their own file should not notify themselves."""
        self.client.post(
            f'/api/v1/files/{self.file.uuid}/comments',
            {'body': 'My own comment'},
            format='json',
        )
        self.assertEqual(self._notifs_for(self.owner).count(), 0)

    def test_comment_notifies_other_commenters(self):
        """Other commenters (not the author) also get notified."""
        third_user = User.objects.create_user(
            username='third', email='third@example.com', password='pass123',
        )
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.recipient,
        )
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=third_user,
        )
        # recipient comments first
        FileComment.objects.create(
            file=self.file, author=self.recipient, body='First!',
        )
        # third_user comments
        self.client.force_authenticate(user=third_user)
        self.client.post(
            f'/api/v1/files/{self.file.uuid}/comments',
            {'body': 'Second comment'},
            format='json',
        )
        # Both owner and recipient should be notified
        self.assertEqual(self._notifs_for(self.owner).count(), 1)
        self.assertEqual(self._notifs_for(self.recipient).count(), 1)
        # third_user should NOT be notified (they're the author)
        self.assertEqual(self._notifs_for(third_user).count(), 0)

    def test_comment_does_not_notify_deleted_commenters(self):
        """Soft-deleted comments should not trigger notifications to their authors."""
        from django.utils import timezone
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.recipient,
        )
        # recipient comments then deletes
        FileComment.objects.create(
            file=self.file, author=self.recipient, body='Deleted',
            deleted_at=timezone.now(),
        )
        # owner comments
        self.client.post(
            f'/api/v1/files/{self.file.uuid}/comments',
            {'body': 'Reply'},
            format='json',
        )
        # recipient should NOT be notified (their comment was deleted)
        self.assertEqual(self._notifs_for(self.recipient).count(), 0)


class SharedEditNotificationTests(FileNotificationTestBase):
    """Tests for notifications when a shared user edits file content."""

    def test_shared_edit_notifies_owner(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.recipient,
            permission='rw',
        )
        self.client.force_authenticate(user=self.recipient)
        new_content = SimpleUploadedFile('doc.txt', b'Updated', content_type='text/plain')
        resp = self.client.patch(
            f'/api/v1/files/{self.file.uuid}',
            {'content': new_content},
            format='multipart',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notifs = self._notifs_for(self.owner)
        self.assertEqual(notifs.count(), 1)
        n = notifs.first()
        self.assertIn('edited', n.title)
        self.assertIn('doc.txt', n.title)
        self.assertEqual(n.actor, self.recipient)

    def test_owner_edit_no_notification(self):
        """Owner editing their own file should not create a notification."""
        new_content = SimpleUploadedFile('doc.txt', b'Updated by owner', content_type='text/plain')
        self.client.patch(
            f'/api/v1/files/{self.file.uuid}',
            {'content': new_content},
            format='multipart',
        )
        self.assertEqual(self._notifs_for(self.owner).count(), 0)


class DestroyNotificationTests(FileNotificationTestBase):
    """Tests for notifications when a file with shares is deleted."""

    def test_delete_file_notifies_shared_users(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.recipient,
        )
        resp = self.client.delete(f'/api/v1/files/{self.file.uuid}')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        notifs = self._notifs_for(self.recipient)
        self.assertEqual(notifs.count(), 1)
        n = notifs.first()
        self.assertIn('deleted', n.title)
        self.assertIn('doc.txt', n.title)
        self.assertEqual(n.url, '')  # no url since file is deleted

    def test_delete_file_notifies_multiple_shared_users(self):
        third_user = User.objects.create_user(
            username='third', email='third@example.com', password='pass123',
        )
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.recipient,
        )
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=third_user,
        )
        self.client.delete(f'/api/v1/files/{self.file.uuid}')
        self.assertEqual(self._notifs_for(self.recipient).count(), 1)
        self.assertEqual(self._notifs_for(third_user).count(), 1)

    def test_delete_file_without_shares_no_notification(self):
        self.client.delete(f'/api/v1/files/{self.file.uuid}')
        self.assertEqual(Notification.objects.filter(origin='files').count(), 0)

    def test_delete_does_not_notify_owner(self):
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.recipient,
        )
        self.client.delete(f'/api/v1/files/{self.file.uuid}')
        self.assertEqual(self._notifs_for(self.owner).count(), 0)
