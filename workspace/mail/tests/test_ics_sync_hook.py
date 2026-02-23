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
    def test_sync_calls_ics_processor_for_flagged_messages(self, mock_process, mock_connect):
        from workspace.mail.services.imap import sync_folder_messages

        msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<test@example.com>', imap_uid=1,
            subject='Invite', date='2026-03-01T10:00:00Z',
            has_calendar_event=True,
        )

        conn = MagicMock()
        conn.uid.return_value = ('OK', [b'1'])
        conn.select.return_value = ('OK', [b'1'])
        mock_connect.return_value = conn

        sync_folder_messages(self.account, self.folder)

        mock_process.assert_called_once()
        processed_msgs = list(mock_process.call_args[0][0])
        self.assertEqual(len(processed_msgs), 1)
        self.assertEqual(processed_msgs[0].pk, msg.pk)
