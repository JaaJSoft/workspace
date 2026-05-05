"""Regression tests for workspace.mail.services.imap_sync.sync_account.

Specifically pin down that per-folder errors are surfaced via
account.last_sync_error rather than being silently masked by the final clear.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.imap_sync import sync_account

User = get_user_model()


class SyncAccountErrorReportingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='syncuser', password='pass')
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='user@example.com',
            last_sync_error='',
        )
        self.account.set_password('secret')
        self.account.save()
        self.inbox = MailFolder.objects.create(
            account=self.account, name='INBOX', display_name='Inbox', folder_type='inbox',
        )
        self.archive = MailFolder.objects.create(
            account=self.account, name='Archive', display_name='Archive', folder_type='archive',
        )

    @mock.patch('workspace.mail.services.imap_sync.sync_folder_messages')
    @mock.patch('workspace.mail.services.imap_sync.sync_folders')
    def test_clears_error_when_all_folders_succeed(self, _folders, _messages):
        self.account.last_sync_error = 'previous error'
        self.account.save(update_fields=['last_sync_error'])

        sync_account(self.account)

        self.account.refresh_from_db()
        self.assertEqual(self.account.last_sync_error, '')
        self.assertIsNotNone(self.account.last_sync_at)

    @mock.patch('workspace.mail.services.imap_sync.sync_folder_messages')
    @mock.patch('workspace.mail.services.imap_sync.sync_folders')
    def test_records_error_when_a_folder_fails(self, _folders, sync_messages):
        def _maybe_fail(account, folder):
            if folder.name == 'INBOX':
                raise RuntimeError('IMAP connection lost')

        sync_messages.side_effect = _maybe_fail

        sync_account(self.account)

        self.account.refresh_from_db()
        self.assertNotEqual(
            self.account.last_sync_error, '',
            "last_sync_error must not be cleared when a folder sync fails",
        )
        self.assertIsNotNone(self.account.last_sync_at)
