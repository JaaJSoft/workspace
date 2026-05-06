"""Regression tests for MailMessageDetailView (_get_message helper).

Pins down that GET/PATCH/DELETE on /api/v1/mail/messages/<uuid> never operate
on soft-deleted messages, since they are not user-visible and acting on them
would re-introduce stale state on the next sync.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class MessageDetailSoftDeletedTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='msguser', password='pass')
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='user@example.com',
        )
        self.account.set_password('secret')
        self.account.save()
        self.inbox = MailFolder.objects.create(
            account=self.account, name='INBOX',
            display_name='Inbox', folder_type='inbox',
        )
        self.deleted_msg = MailMessage.objects.create(
            account=self.account, folder=self.inbox, imap_uid=42,
            deleted_at=timezone.now(),
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _url(self):
        return f'/api/v1/mail/messages/{self.deleted_msg.uuid}'

    def test_get_returns_404_for_soft_deleted(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 404)

    def test_patch_returns_404_for_soft_deleted(self):
        resp = self.client.patch(self._url(), {'is_starred': True}, format='json')
        self.assertEqual(resp.status_code, 404)
        # Defensive: a 404 must not have applied the patch.
        self.deleted_msg.refresh_from_db()
        self.assertFalse(self.deleted_msg.is_starred)
