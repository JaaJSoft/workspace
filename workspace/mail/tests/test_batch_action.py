from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()

PARAMETER_LIMIT = 50


class BatchActionLargeSelectionTests(TestCase):
    """A selection of more messages than SQLite binds in one statement."""

    def setUp(self):
        self.user = User.objects.create_user(username="batchuser", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.messages = MailMessage.objects.bulk_create(
            MailMessage(
                account=self.account, folder=self.inbox, imap_uid=uid, is_read=False
            )
            for uid in range(1, PARAMETER_LIMIT * 2 + 1)
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("workspace.mail.services.imap_messages.mark_read")
    def test_marks_the_whole_selection_read(self, _mark_read):
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            resp = self.client.post(
                "/api/v1/mail/messages/batch-action",
                {
                    "message_ids": [str(message.uuid) for message in self.messages],
                    "action": "mark_read",
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["processed"], len(self.messages))
        unread = MailMessage.objects.filter(folder=self.inbox, is_read=False)
        self.assertFalse(unread.exists())
