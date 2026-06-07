"""Regression tests for the batch move action.

Pin down that an IMAP move failure leaves the local row pointing at its
original folder, otherwise DB and IMAP go out of sync: the next reconcile
would soft-delete the message that's still actually present on the server.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class BatchMoveIMAPFailureTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="moveuser", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.archive = MailFolder.objects.create(
            account=self.account,
            name="Archive",
            display_name="Archive",
            folder_type="archive",
        )
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=42,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("workspace.mail.services.imap_messages.move_message")
    def test_imap_move_failure_leaves_folder_unchanged(self, mock_move):
        """If move_message raises, the message must stay in its source folder
        on the DB side: updating msg.folder optimistically and never rolling
        back creates a split-brain that the next sync mishandles."""
        mock_move.side_effect = RuntimeError("IMAP MOVE timeout")

        resp = self.client.post(
            "/api/v1/mail/messages/batch-action",
            {
                "message_ids": [str(self.msg.uuid)],
                "action": "move",
                "target_folder_id": str(self.archive.uuid),
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        # processed should not count this message - the move did not actually happen.
        self.assertEqual(resp.data.get("processed", 0), 0)

        self.msg.refresh_from_db()
        self.assertEqual(
            self.msg.folder_id,
            self.inbox.pk,
            "Message must remain in source folder when IMAP move fails",
        )

    @patch("workspace.mail.services.imap_messages.move_message")
    def test_imap_move_success_updates_folder(self, mock_move):
        """Sanity check: when move_message succeeds, the folder is updated."""
        mock_move.return_value = None

        resp = self.client.post(
            "/api/v1/mail/messages/batch-action",
            {
                "message_ids": [str(self.msg.uuid)],
                "action": "move",
                "target_folder_id": str(self.archive.uuid),
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("processed", 0), 1)
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder_id, self.archive.pk)
