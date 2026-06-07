"""Regression tests for workspace.mail.services.imap_messages.save_draft.

The critical invariant: an existing draft (old_uid) must not be deleted on the
IMAP server until the new draft has been APPENDed successfully. Otherwise a
network/quota failure mid-save would lose both copies.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.imap_messages import save_draft

User = get_user_model()

RAW_DRAFT = (
    b"From: a@example.com\r\n"
    b"To: b@example.com\r\n"
    b"Subject: hi\r\n"
    b"Message-ID: <our-draft-id@example.com>\r\n"
    b"\r\nbody"
)


class SaveDraftTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="draftuser", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.drafts = MailFolder.objects.create(
            account=self.account,
            name="Drafts",
            display_name="Drafts",
            folder_type=MailFolder.FolderType.DRAFTS,
        )

    def _mock_conn(self, append_status="OK"):
        conn = MagicMock()
        conn.select.return_value = ("OK", [b""])
        conn.append.return_value = (append_status, [b"APPENDUID 1 99"])
        conn.uid.return_value = ("OK", [b""])
        conn.expunge.return_value = ("OK", [b""])
        return conn

    @patch("workspace.mail.services.imap_sync.sync_folder_messages")
    @patch("workspace.mail.services.imap_messages.connect_imap")
    def test_append_failure_leaves_old_draft_intact(self, mock_connect, _sync):
        """If APPEND fails, the old draft must NOT be STOREd as deleted nor
        EXPUNGEd; otherwise the user loses both old and new drafts."""
        conn = self._mock_conn(append_status="NO")
        mock_connect.return_value = conn

        result = save_draft(self.account, RAW_DRAFT, old_uid=42)

        self.assertIsNone(result)
        conn.append.assert_called_once()
        conn.uid.assert_not_called()
        conn.expunge.assert_not_called()

    @patch("workspace.mail.services.imap_sync.sync_folder_messages")
    @patch("workspace.mail.services.imap_messages.connect_imap")
    def test_append_success_then_deletes_old_draft(self, mock_connect, _sync):
        """On APPEND success with old_uid, the old draft is STOREd \\Deleted
        and EXPUNGEd, in that order, after APPEND."""
        conn = self._mock_conn(append_status="OK")
        mock_connect.return_value = conn
        call_order = []
        conn.append.side_effect = lambda *a, **kw: (
            call_order.append("append") or ("OK", [b""])
        )
        conn.uid.side_effect = lambda *a, **kw: (
            call_order.append(("uid",) + a) or ("OK", [b""])
        )
        conn.expunge.side_effect = lambda *a, **kw: (
            call_order.append("expunge") or ("OK", [b""])
        )

        save_draft(self.account, RAW_DRAFT, old_uid=42)

        self.assertEqual(call_order[0], "append")
        self.assertEqual(call_order[1], ("uid", "STORE", "42", "+FLAGS", "(\\Deleted)"))
        self.assertEqual(call_order[2], "expunge")

    @patch("workspace.mail.services.imap_sync.sync_folder_messages")
    @patch("workspace.mail.services.imap_messages.connect_imap")
    def test_no_old_uid_skips_delete(self, mock_connect, _sync):
        """A first-time draft save (no old_uid) must not call STORE/EXPUNGE."""
        conn = self._mock_conn(append_status="OK")
        mock_connect.return_value = conn

        save_draft(self.account, RAW_DRAFT, old_uid=None)

        conn.append.assert_called_once()
        conn.uid.assert_not_called()
        conn.expunge.assert_not_called()

    @patch("workspace.mail.services.imap_sync.sync_folder_messages")
    @patch("workspace.mail.services.imap_messages.connect_imap")
    def test_returns_appended_draft_not_concurrent_intruder(
        self, mock_connect, mock_sync
    ):
        """If a parallel IMAP session APPENDed a different draft and the sync
        picked both up, we must return OUR draft (matched by Message-ID), not
        the intruder, even when the intruder has a more recent created_at."""
        from workspace.mail.models import MailMessage

        conn = self._mock_conn(append_status="OK")
        mock_connect.return_value = conn

        def _fake_sync(account, folder):
            # The intruder is created LAST (most recent created_at) - exactly
            # the scenario where order_by('-created_at') would return it.
            MailMessage.objects.create(
                account=account,
                folder=folder,
                imap_uid=100,
                message_id="<our-draft-id@example.com>",
            )
            MailMessage.objects.create(
                account=account,
                folder=folder,
                imap_uid=101,
                message_id="<intruder@other-client.example.com>",
            )

        mock_sync.side_effect = _fake_sync

        result = save_draft(self.account, RAW_DRAFT, old_uid=None)

        self.assertIsNotNone(result)
        self.assertEqual(result.message_id, "<our-draft-id@example.com>")
        self.assertEqual(result.imap_uid, 100)
