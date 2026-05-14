from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class SyncExtractDispatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='s', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='s@x.com',
            imap_host='x', imap_port=993, smtp_host='x', smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )

    @patch('workspace.ai.services.dispatch.dispatch')
    @patch('workspace.users.services.settings.get_setting', return_value=True)
    @patch('workspace.ai.client.is_ai_enabled', return_value=True)
    @patch('workspace.calendar.services.ics_processor.process_calendar_emails')
    @patch('workspace.mail.services.imap_sync._reconcile_folder')
    @patch('workspace.mail.services.imap_sync._parse_message')
    @patch('workspace.mail.services.imap_sync.connect_imap')
    def test_extract_dispatched_for_new_messages(
        self, mock_conn, mock_parse, _mock_recon, _mock_proc,
        _mock_ai, _mock_setting, mock_dispatch,
    ):
        from workspace.mail.services.imap_sync import sync_folder_messages

        new_msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<n@x>', imap_uid=2, subject='S', date='2026-05-14T10:00:00Z',
        )
        mock_parse.return_value = new_msg

        conn = MagicMock()
        conn.uid.side_effect = [
            ('OK', [b'2']),
            ('OK', [(b'2 (UID 2 FLAGS ())', b'fake'), b')']),
        ]
        conn.select.return_value = ('OK', [b'1'])
        mock_conn.return_value = conn

        sync_folder_messages(self.account, self.folder)

        task_types = [c.kwargs.get('task_type') for c in mock_dispatch.call_args_list]
        from workspace.ai.models import AITask
        self.assertIn(AITask.TaskType.CLASSIFY, task_types)
        self.assertIn(AITask.TaskType.EXTRACT, task_types)

    @patch('workspace.ai.services.dispatch.dispatch')
    @patch('workspace.users.services.settings.get_setting', return_value=False)
    @patch('workspace.ai.client.is_ai_enabled', return_value=True)
    @patch('workspace.calendar.services.ics_processor.process_calendar_emails')
    @patch('workspace.mail.services.imap_sync._reconcile_folder')
    @patch('workspace.mail.services.imap_sync._parse_message')
    @patch('workspace.mail.services.imap_sync.connect_imap')
    def test_extract_skipped_when_user_disabled_ai(
        self, mock_conn, mock_parse, _mock_recon, _mock_proc,
        _mock_ai, _mock_setting, mock_dispatch,
    ):
        from workspace.mail.services.imap_sync import sync_folder_messages

        new_msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<n@x>', imap_uid=2, subject='S', date='2026-05-14T10:00:00Z',
        )
        mock_parse.return_value = new_msg

        conn = MagicMock()
        conn.uid.side_effect = [
            ('OK', [b'2']),
            ('OK', [(b'2 (UID 2 FLAGS ())', b'fake'), b')']),
        ]
        conn.select.return_value = ('OK', [b'1'])
        mock_conn.return_value = conn

        sync_folder_messages(self.account, self.folder)

        mock_dispatch.assert_not_called()
