"""Sync dedup contract: already-synced UIDs are skipped without
per-message existence queries (regression for the FETCH-loop N+1)."""

import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services import imap_sync
from workspace.mail.services.imap_sync import sync_folder_messages
from workspace.mail.tests.imap_fake import fake_imap

User = get_user_model()


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
        with fake_imap() as server:
            for uid in uids:
                server.deliver(
                    "INBOX",
                    uid=uid,
                    subject="hello",
                    message_id=f"<m{uid}@example.com>",
                )
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
        error log. Simulated by inserting the row, as the other sync
        would, between this one's snapshot and its own insert."""
        parse = imap_sync._parse_message

        def parse_after_the_other_sync(raw, account, folder, uid, flags, **kwargs):
            if uid == 2:
                MailMessage.objects.create(account=account, folder=folder, imap_uid=2)
            return parse(raw, account, folder, uid, flags, **kwargs)

        with (
            patch.object(
                imap_sync, "_parse_message", side_effect=parse_after_the_other_sync
            ),
            self.assertNoLogs("workspace.mail.services.imap_sync", level="ERROR"),
        ):
            self._run_sync([1, 2])
        self.assertEqual(
            MailMessage.objects.filter(folder=self.folder, imap_uid=2).count(), 1
        )
        # The colliding UID still advances the sync cursor: the message
        # is in the DB, re-fetching it forever would be the old bug.
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.last_sync_uid, 2)
