"""Regression test for the MailDraftView DELETE handler.

Pins down that an IMAP delete failure does not leave the draft active in DB:
the view must fall back to a local soft-delete so the user's intent is
honored and the UI doesn't flicker the draft back on refresh.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class DeleteDraftIMAPFailureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='draftdel', password='pass')
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='user@example.com',
        )
        self.account.set_password('secret')
        self.account.save()
        self.drafts = MailFolder.objects.create(
            account=self.account, name='Drafts',
            display_name='Drafts', folder_type='drafts',
        )
        self.draft = MailMessage.objects.create(
            account=self.account, folder=self.drafts, imap_uid=42,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch('workspace.mail.services.imap_messages.delete_draft')
    def test_imap_failure_falls_back_to_local_soft_delete(self, mock_delete):
        """When delete_draft raises, the view must soft-delete locally and
        still return 204 - otherwise the user sees the draft re-appear on
        the next page refresh."""
        mock_delete.side_effect = RuntimeError('IMAP timeout')

        resp = self.client.delete(f'/api/v1/mail/drafts/{self.draft.uuid}')

        self.assertEqual(resp.status_code, 204)
        self.draft.refresh_from_db()
        self.assertIsNotNone(
            self.draft.deleted_at,
            "Draft must be soft-deleted in DB even when IMAP delete fails",
        )

    @patch('workspace.mail.services.imap_messages.delete_draft')
    def test_imap_success_path_unchanged(self, mock_delete):
        """Sanity check: when delete_draft succeeds (and itself sets
        deleted_at), the view returns 204 with the draft soft-deleted."""
        from django.utils import timezone

        def _succeed(account, msg):
            msg.deleted_at = timezone.now()
            msg.save(update_fields=['deleted_at', 'updated_at'])
        mock_delete.side_effect = _succeed

        resp = self.client.delete(f'/api/v1/mail/drafts/{self.draft.uuid}')

        self.assertEqual(resp.status_code, 204)
        self.draft.refresh_from_db()
        self.assertIsNotNone(self.draft.deleted_at)
