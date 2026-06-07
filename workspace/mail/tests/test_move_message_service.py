"""Regression tests for workspace.mail.services.imap_messages.move_message.

Critical invariant: if the IMAP COPY step fails, the source message must
NOT be marked \\Deleted nor expunged - otherwise the message is lost with
no copy at the target.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_messages import move_message

User = get_user_model()


class MoveMessageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="moveu", password="pass")
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

    def _mock_conn(self, copy_status="OK"):
        conn = MagicMock()
        conn.select.return_value = ("OK", [b""])

        def uid_side_effect(cmd, *args):
            if cmd == "COPY":
                return (
                    copy_status,
                    [b"COPYUID 1 42 100" if copy_status == "OK" else b"over quota"],
                )
            if cmd == "STORE":
                return ("OK", [b""])
            return ("OK", [b""])

        conn.uid.side_effect = uid_side_effect
        conn.expunge.return_value = ("OK", [b""])
        return conn

    @patch("workspace.mail.services.imap_messages.connect_imap")
    def test_copy_failure_does_not_delete_source(self, mock_connect):
        """If COPY returns NO, the source must NOT be STOREd \\Deleted nor
        expunged - otherwise the message is permanently lost."""
        conn = self._mock_conn(copy_status="NO")
        mock_connect.return_value = conn

        with self.assertRaises(Exception):
            move_message(self.account, self.msg, self.archive)

        # Verify STORE and EXPUNGE were never called: only the COPY attempt
        # should appear in the uid call list.
        store_calls = [c for c in conn.uid.call_args_list if c.args[0] == "STORE"]
        self.assertEqual(store_calls, [], "STORE must not run after a failed COPY")
        conn.expunge.assert_not_called()

    @patch("workspace.mail.services.imap_messages.connect_imap")
    def test_copy_success_runs_store_and_expunge(self, mock_connect):
        """Sanity check: when COPY succeeds, STORE \\Deleted and EXPUNGE
        must follow in that order to complete the move."""
        conn = self._mock_conn(copy_status="OK")
        mock_connect.return_value = conn
        call_order = []

        def uid_side_effect(cmd, *args):
            call_order.append(cmd)
            return ("OK", [b""])

        conn.uid.side_effect = uid_side_effect

        def expunge_side_effect(*a, **kw):
            call_order.append("EXPUNGE")
            return ("OK", [b""])

        conn.expunge.side_effect = expunge_side_effect

        move_message(self.account, self.msg, self.archive)

        self.assertEqual(call_order, ["COPY", "STORE", "EXPUNGE"])
