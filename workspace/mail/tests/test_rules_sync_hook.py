from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class SyncRulesHookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='shu', password='p')
        self.account = MailAccount.objects.create(
            owner=self.user, email='sh@x.com',
            imap_host='x', smtp_host='x', username='sh@x.com',
        )
        self.folder = MailFolder.objects.create(
            account=self.account, name='INBOX', folder_type='inbox',
        )

    def _mock_imap_conn(self):
        conn = MagicMock()
        conn.uid.side_effect = [
            ('OK', [b'2']),
            ('OK', [(b'2 (UID 2 FLAGS ())', b'fake'), b')']),
        ]
        conn.select.return_value = ('OK', [b'1'])
        return conn

    @patch('workspace.mail.services.rules.engine.run_rules_for_messages')
    @patch('workspace.ai.services.dispatch.dispatch')
    @patch('workspace.users.services.settings.get_setting', return_value=True)
    @patch('workspace.ai.client.is_ai_enabled', return_value=True)
    @patch('workspace.calendar.services.ics_processor.process_calendar_emails')
    @patch('workspace.mail.services.imap_sync._reconcile_folder')
    @patch('workspace.mail.services.imap_sync._parse_message')
    @patch('workspace.mail.services.imap_sync.connect_imap')
    def test_rules_invoked_for_new_inbox_message(
        self, mock_conn, mock_parse, _mock_recon, _mock_proc,
        _mock_ai, _mock_setting, _mock_dispatch, mock_rules,
    ):
        from workspace.mail.services.imap_sync import sync_folder_messages

        new_msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<a@x>', imap_uid=2, subject='S',
        )
        mock_parse.return_value = new_msg
        mock_conn.return_value = self._mock_imap_conn()

        sync_folder_messages(self.account, self.folder)

        mock_rules.assert_called_once()
        args, _ = mock_rules.call_args
        self.assertEqual(args[0], self.account)
        self.assertIn(str(new_msg.uuid), args[1])

    @patch('workspace.mail.services.rules.engine.run_rules_for_messages')
    @patch('workspace.ai.services.dispatch.dispatch')
    @patch('workspace.users.services.settings.get_setting', return_value=True)
    @patch('workspace.ai.client.is_ai_enabled', return_value=True)
    @patch('workspace.calendar.services.ics_processor.process_calendar_emails')
    @patch('workspace.mail.services.imap_sync._reconcile_folder')
    @patch('workspace.mail.services.imap_sync._parse_message')
    @patch('workspace.mail.services.imap_sync.connect_imap')
    def test_rules_not_invoked_for_sent_folder(
        self, mock_conn, mock_parse, _mock_recon, _mock_proc,
        _mock_ai, _mock_setting, _mock_dispatch, mock_rules,
    ):
        from workspace.mail.services.imap_sync import sync_folder_messages
        sent = MailFolder.objects.create(
            account=self.account, name='Sent', folder_type='sent',
        )
        new_msg = MailMessage.objects.create(
            account=self.account, folder=sent,
            message_id='<a@x>', imap_uid=2, subject='S',
        )
        mock_parse.return_value = new_msg
        mock_conn.return_value = self._mock_imap_conn()
        sync_folder_messages(self.account, sent)
        mock_rules.assert_not_called()
