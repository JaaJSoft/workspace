from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.ai.models import AITask
from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class SyncClassifyExclusionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="c", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="c@x.com",
            imap_host="x",
            smtp_host="x",
            username="c@x.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            folder_type="inbox",
        )

    def tearDown(self):
        cache.clear()

    def _run_sync(self):
        new_msg = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            message_id="<n@x>",
            imap_uid=2,
            subject="S",
            date="2026-05-14T10:00:00Z",
        )

        conn = MagicMock()
        conn.uid.side_effect = [
            ("OK", [b"2"]),
            ("OK", [(b"2 (UID 2 FLAGS ())", b"fake"), b")"]),
        ]
        conn.select.return_value = ("OK", [b"1"])

        from workspace.mail.services.imap_sync import sync_folder_messages

        with (
            patch("workspace.mail.services.imap_sync.connect_imap", return_value=conn),
            patch(
                "workspace.mail.services.imap_sync._parse_message", return_value=new_msg
            ),
            patch("workspace.mail.services.imap_sync._reconcile_folder"),
            patch("workspace.calendar.services.ics_processor.process_calendar_emails"),
            patch("workspace.ai.client.is_ai_enabled", return_value=True),
            # Patch the call site (imap_sync imports this name locally from
            # ai_settings), so feature-enablement is explicit and the tests
            # depend solely on the per-folder ai_classify_disabled flag rather
            # than on the default-True settings fallback.
            patch(
                "workspace.mail.services.ai_settings.is_mail_ai_feature_enabled",
                return_value=True,
            ),
            patch("workspace.ai.services.dispatch.dispatch") as mock_dispatch,
        ):
            sync_folder_messages(self.account, self.folder)
            return mock_dispatch

    def test_classify_dispatched_when_not_excluded(self):
        mock_dispatch = self._run_sync()
        task_types = [c.kwargs.get("task_type") for c in mock_dispatch.call_args_list]
        self.assertIn(AITask.TaskType.CLASSIFY, task_types)

    def test_classify_not_dispatched_when_folder_excluded(self):
        self.folder.ai_classify_disabled = True
        self.folder.save(update_fields=["ai_classify_disabled"])
        mock_dispatch = self._run_sync()
        task_types = [c.kwargs.get("task_type") for c in mock_dispatch.call_args_list]
        self.assertNotIn(AITask.TaskType.CLASSIFY, task_types)

    def test_extract_still_dispatched_when_classify_excluded(self):
        # Proof the exclusion is scoped to labels only: extract must still run.
        self.folder.ai_classify_disabled = True
        self.folder.save(update_fields=["ai_classify_disabled"])
        mock_dispatch = self._run_sync()
        task_types = [c.kwargs.get("task_type") for c in mock_dispatch.call_args_list]
        self.assertIn(AITask.TaskType.EXTRACT, task_types)
        self.assertNotIn(AITask.TaskType.CLASSIFY, task_types)
