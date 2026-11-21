from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.ai.models import AITask
from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.imap_sync import sync_folder_messages
from workspace.mail.tests.imap_fake import fake_imap
from workspace.users.services.settings import set_setting

User = get_user_model()


class SyncExtractDispatchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="s", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="s@x.com",
            imap_host="x",
            imap_port=993,
            smtp_host="x",
            smtp_port=587,
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            folder_type="inbox",
        )

    def tearDown(self):
        cache.clear()

    def _run_sync(self, dispatch_side_effect=None, folder=None):
        """Sync one new message; returns the AI dispatch mock."""
        folder = folder or self.folder
        with (
            fake_imap() as server,
            patch("workspace.calendar.services.ics_processor.process_calendar_emails"),
            patch("workspace.ai.client.is_ai_enabled", return_value=True),
            patch(
                "workspace.ai.services.dispatch.dispatch",
                side_effect=dispatch_side_effect,
            ) as mock_dispatch,
        ):
            server.deliver(folder.name)
            sync_folder_messages(self.account, folder)
            return mock_dispatch

    def test_both_dispatched_by_default(self):
        mock_dispatch = self._run_sync()
        task_types = [c.kwargs.get("task_type") for c in mock_dispatch.call_args_list]
        self.assertIn(AITask.TaskType.CLASSIFY, task_types)
        self.assertIn(AITask.TaskType.EXTRACT, task_types)

    def test_only_classify_dispatched_when_extract_disabled(self):
        set_setting(self.user, "mail", "ai_extract", False)
        mock_dispatch = self._run_sync()
        task_types = [c.kwargs.get("task_type") for c in mock_dispatch.call_args_list]
        self.assertIn(AITask.TaskType.CLASSIFY, task_types)
        self.assertNotIn(AITask.TaskType.EXTRACT, task_types)

    def test_only_extract_dispatched_when_classify_disabled(self):
        set_setting(self.user, "mail", "ai_classify", False)
        mock_dispatch = self._run_sync()
        task_types = [c.kwargs.get("task_type") for c in mock_dispatch.call_args_list]
        self.assertNotIn(AITask.TaskType.CLASSIFY, task_types)
        self.assertIn(AITask.TaskType.EXTRACT, task_types)

    def test_neither_dispatched_when_both_disabled(self):
        set_setting(self.user, "mail", "ai_classify", False)
        set_setting(self.user, "mail", "ai_extract", False)
        mock_dispatch = self._run_sync()
        mock_dispatch.assert_not_called()

    def test_extract_still_dispatched_when_classify_dispatch_raises(self):
        # If the classify dispatch blows up, extract must still run - they're
        # independently gated AND independently wrapped.
        def dispatch_side_effect(*args, **kwargs):
            if kwargs.get("task_type") == AITask.TaskType.CLASSIFY:
                raise RuntimeError("classify dispatch failed")
            return None

        mock_dispatch = self._run_sync(dispatch_side_effect)

        task_types = [c.kwargs.get("task_type") for c in mock_dispatch.call_args_list]
        # Both attempted, classify failed but extract was still called.
        self.assertEqual(
            task_types, [AITask.TaskType.CLASSIFY, AITask.TaskType.EXTRACT]
        )

    def test_nothing_dispatched_for_folders_that_receive_no_new_mail(self):
        for folder_type in ("sent", "drafts", "spam", "trash"):
            with self.subTest(folder_type=folder_type):
                folder = MailFolder.objects.create(
                    account=self.account, name=folder_type, folder_type=folder_type
                )
                self._run_sync(folder=folder).assert_not_called()
