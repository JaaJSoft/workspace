"""Sync dedup contract: already-synced UIDs are skipped without
per-message existence queries (regression for the FETCH-loop N+1)."""

import re
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()

RAW_EMAIL = (
    b"From: sender@example.com\r\n"
    b"To: me@example.com\r\n"
    b"Subject: hello\r\n"
    b"Message-ID: <m%d@example.com>\r\n"
    b"\r\n"
    b"body\r\n"
)


class SyncDedupTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="dd", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="dd@x.com",
            imap_host="x",
            smtp_host="x",
            username="dd@x.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            folder_type="inbox",
            last_sync_uid=0,
        )

    def _run_sync(self, uids):
        from workspace.mail.services.imap_sync import sync_folder_messages

        search_bytes = b" ".join(str(u).encode() for u in uids)
        fetch_parts = [(f"1 (UID {u} FLAGS ())".encode(), RAW_EMAIL % u) for u in uids]
        conn = MagicMock()
        conn.select.return_value = ("OK", [b"1"])
        conn.uid.side_effect = [
            ("OK", [search_bytes]),
            ("OK", fetch_parts),
        ]
        with (
            patch("workspace.mail.services.imap_sync._reconcile_folder"),
            patch("workspace.mail.services.imap_sync.connect_imap", return_value=conn),
        ):
            sync_folder_messages(self.account, self.folder)

    def test_existing_uid_not_duplicated(self):
        MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=2,
            subject="already here",
        )
        self._run_sync([1, 2, 3])
        self.assertEqual(MailMessage.objects.filter(folder=self.folder).count(), 3)
        self.assertEqual(
            MailMessage.objects.filter(folder=self.folder, imap_uid=2).count(), 1
        )
        # The pre-existing row was not overwritten by the fetched copy.
        kept = MailMessage.objects.get(folder=self.folder, imap_uid=2)
        self.assertEqual(kept.subject, "already here")

    def test_no_per_message_existence_query(self):
        """Existence must be checked once per FETCH batch (imap_uid IN ...),
        never one probe per message (imap_uid = ...)."""
        MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=2,
            subject="already here",
        )
        with CaptureQueriesContext(connection) as ctx:
            self._run_sync([1, 2, 3])
        # Quote-agnostic on purpose: identifier quoting varies by backend
        # ("imap_uid", `imap_uid`, bare), and the probe must be caught in
        # all of them. The batch lookup uses IN (...), which never matches.
        per_message_probes = [
            q
            for q in ctx.captured_queries
            if re.search(r'[`"]?imap_uid[`"]?\s*=', q["sql"])
        ]
        self.assertEqual(
            len(per_message_probes),
            0,
            f"per-message UID probes found: {len(per_message_probes)}",
        )

    def test_duplicate_insert_race_skipped_quietly(self):
        """Concurrent syncs of one folder can both miss a UID in their
        batch snapshot and race to insert it; the loser hits the
        (folder, imap_uid) unique constraint. That collision is an
        expected skip, not a parse failure, so it must not reach the
        error log. Simulated by repeating a UID inside one FETCH batch:
        the snapshot predates both copies, the second insert collides."""
        with self.assertNoLogs("workspace.mail.services.imap_sync", level="ERROR"):
            self._run_sync([1, 2, 2])
        self.assertEqual(
            MailMessage.objects.filter(folder=self.folder, imap_uid=2).count(), 1
        )
        # The colliding UID still advances the sync cursor: the message
        # is in the DB, re-fetching it forever would be the old bug.
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.last_sync_uid, 2)
