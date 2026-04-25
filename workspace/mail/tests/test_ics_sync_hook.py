from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()

class SyncCalendarHookTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='bob', email='bob@test.com', password='pass123')
        self.account = MailAccount.objects.create(
            owner=self.user, email='bob@test.com',
            imap_host='imap.test.com', imap_port=993, imap_use_ssl=True,
            smtp_host='smtp.test.com', smtp_port=587, smtp_use_tls=True,
            username='bob@test.com',
        )
        self.account.set_password('pass')
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', display_name='Inbox',
            folder_type='inbox', uid_validity=1, last_sync_uid=0,
        )

    @patch('workspace.mail.services.imap.connect_imap')
    @patch('workspace.calendar.services.ics_processor.process_calendar_emails')
    def test_sync_skips_ics_processor_when_no_new_messages(self, mock_process, mock_connect):
        """Pre-existing ICS messages are NOT re-processed on every sync.

        Calendar reprocessing must be scoped to messages actually parsed in
        the current sync pass, otherwise every sync pays O(all_cal_messages).
        """
        from workspace.mail.services.imap import sync_folder_messages

        MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<old@example.com>', imap_uid=1,
            subject='Old Invite', date='2026-03-01T10:00:00Z',
            has_calendar_event=True,
        )

        conn = MagicMock()
        conn.uid.return_value = ('OK', [b'1'])
        conn.select.return_value = ('OK', [b'1'])
        mock_connect.return_value = conn

        sync_folder_messages(self.account, self.folder)

        # No new UIDs parsed in this pass → no calendar reprocessing.
        mock_process.assert_not_called()

    @patch('workspace.ai.tasks.classify_mail_messages.delay')
    @patch('workspace.ai.client.is_ai_enabled', return_value=False)
    @patch('workspace.mail.services.imap._reconcile_folder')
    @patch('workspace.mail.services.imap._parse_message')
    @patch('workspace.mail.services.imap.connect_imap')
    @patch('workspace.calendar.services.ics_processor.process_calendar_emails')
    def test_sync_calls_ics_processor_for_newly_parsed_messages(
        self, mock_process, mock_connect, mock_parse, _mock_reconcile,
        _mock_ai_enabled, _mock_classify_delay,
    ):
        """Newly parsed ICS messages are handed to process_calendar_emails."""
        from workspace.mail.services.imap import sync_folder_messages

        # Pretend UID 2 is a fresh ICS email that _parse_message returns.
        new_msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<new@example.com>', imap_uid=2,
            subject='New Invite', date='2026-03-01T10:00:00Z',
            has_calendar_event=True,
        )
        mock_parse.return_value = new_msg

        conn = MagicMock()
        # SEARCH returns one new UID; FETCH returns a (headers, body) tuple
        # so the parse loop reaches _parse_message. Reconciliation is
        # patched out — its IMAP details don't matter here.
        conn.uid.side_effect = [
            ('OK', [b'2']),
            ('OK', [(b'2 (UID 2 FLAGS ())', b'fake raw email'), b')']),
        ]
        conn.select.return_value = ('OK', [b'1'])
        mock_connect.return_value = conn

        sync_folder_messages(self.account, self.folder)

        mock_process.assert_called_once()
        processed = list(mock_process.call_args[0][0])
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0].pk, new_msg.pk)
