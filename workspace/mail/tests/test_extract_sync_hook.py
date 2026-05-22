from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.ai.models import AITask
from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.users.services.settings import set_setting

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

    def tearDown(self):
        cache.clear()

    def _run_sync(self):
        new_msg = MailMessage.objects.create(
            account=self.account, folder=self.folder,
            message_id='<n@x>', imap_uid=2, subject='S', date='2026-05-14T10:00:00Z',
        )

        conn = MagicMock()
        conn.uid.side_effect = [
            ('OK', [b'2']),
            ('OK', [(b'2 (UID 2 FLAGS ())', b'fake'), b')']),
        ]
        conn.select.return_value = ('OK', [b'1'])

        from workspace.mail.services.imap_sync import sync_folder_messages

        with patch('workspace.mail.services.imap_sync.connect_imap', return_value=conn), \
             patch('workspace.mail.services.imap_sync._parse_message', return_value=new_msg), \
             patch('workspace.mail.services.imap_sync._reconcile_folder'), \
             patch('workspace.calendar.services.ics_processor.process_calendar_emails'), \
             patch('workspace.ai.client.is_ai_enabled', return_value=True), \
             patch('workspace.ai.services.dispatch.dispatch') as mock_dispatch:
            sync_folder_messages(self.account, self.folder)
            return mock_dispatch

    def test_both_dispatched_by_default(self):
        mock_dispatch = self._run_sync()
        task_types = [c.kwargs.get('task_type') for c in mock_dispatch.call_args_list]
        self.assertIn(AITask.TaskType.CLASSIFY, task_types)
        self.assertIn(AITask.TaskType.EXTRACT, task_types)

    def test_only_classify_dispatched_when_extract_disabled(self):
        set_setting(self.user, 'mail', 'ai_extract', False)
        mock_dispatch = self._run_sync()
        task_types = [c.kwargs.get('task_type') for c in mock_dispatch.call_args_list]
        self.assertIn(AITask.TaskType.CLASSIFY, task_types)
        self.assertNotIn(AITask.TaskType.EXTRACT, task_types)

    def test_only_extract_dispatched_when_classify_disabled(self):
        set_setting(self.user, 'mail', 'ai_classify', False)
        mock_dispatch = self._run_sync()
        task_types = [c.kwargs.get('task_type') for c in mock_dispatch.call_args_list]
        self.assertNotIn(AITask.TaskType.CLASSIFY, task_types)
        self.assertIn(AITask.TaskType.EXTRACT, task_types)

    def test_neither_dispatched_when_both_disabled(self):
        set_setting(self.user, 'mail', 'ai_classify', False)
        set_setting(self.user, 'mail', 'ai_extract', False)
        mock_dispatch = self._run_sync()
        mock_dispatch.assert_not_called()

    def test_legacy_ai_enabled_false_disables_both(self):
        # Users who turned the single ai_enabled toggle off before the split
        # must keep everything off until they explicitly opt in to a feature.
        set_setting(self.user, 'mail', 'ai_enabled', False)
        mock_dispatch = self._run_sync()
        mock_dispatch.assert_not_called()
