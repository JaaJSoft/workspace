from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from imapclient.exceptions import IMAPClientError

from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_sync import _reconcile_folder, sync_folder_messages
from workspace.mail.tests.imap_fake import FakeImapServer, fake_imap
from workspace.notifications.models import Notification

User = get_user_model()


class ReconcileFolderMixin:
    """Common setup for reconciliation tests."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="reconcileuser",
            email="reconcile@test.com",
            password="pass",
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            imap_use_ssl=True,
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )

    def _make_msg(
        self, uid, is_read=False, is_starred=False, is_answered=False, deleted_at=None
    ):
        return MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=uid,
            is_read=is_read,
            is_starred=is_starred,
            is_answered=is_answered,
            deleted_at=deleted_at,
        )

    def _mock_conn(self, remote_uids, flags=None):
        """A connection with the folder's mailbox selected.

        ``remote_uids`` - the UIDs the server holds, in ascending order.
        ``flags``       - optional dict {uid: flags} as a FETCH answer spells
                          them, separated by spaces. No flag by default.
        """
        server = FakeImapServer()
        for uid in remote_uids:
            server.deliver(
                self.folder.name, uid=uid, flags=(flags or {}).get(uid, "").split()
            )
        conn = server.connect()
        conn.select_folder(self.folder.name, readonly=True)
        return conn


def _stored_uids(folder):
    return set(
        MailMessage.objects.filter(folder=folder, deleted_at__isnull=True).values_list(
            "imap_uid", flat=True
        )
    )


def _reconcile(conn, folder, changed_since=None):
    """Reconcile `folder` with everything the selected mailbox holds."""
    _reconcile_folder(
        conn,
        folder,
        _stored_uids(folder),
        conn.search("ALL"),
        changed_since=changed_since,
    )


class ReconcileSoftDeleteTests(ReconcileFolderMixin, TestCase):
    """Tests for soft-deleting messages whose UIDs are gone from server."""

    def test_soft_deletes_gone_uids(self):
        """Messages whose UIDs no longer exist on server are soft-deleted."""
        msg1 = self._make_msg(100)
        msg2 = self._make_msg(200)
        msg3 = self._make_msg(300)

        # Server only has UID 200 — 100 and 300 are gone
        conn = self._mock_conn([200])
        _reconcile(conn, self.folder)

        msg1.refresh_from_db()
        msg2.refresh_from_db()
        msg3.refresh_from_db()
        self.assertIsNotNone(msg1.deleted_at)
        self.assertIsNone(msg2.deleted_at)
        self.assertIsNotNone(msg3.deleted_at)

    def test_preserves_all_when_all_present(self):
        """No messages are deleted when all UIDs exist on server."""
        msg1 = self._make_msg(100)
        msg2 = self._make_msg(200)

        conn = self._mock_conn([100, 200])
        _reconcile(conn, self.folder)

        msg1.refresh_from_db()
        msg2.refresh_from_db()
        self.assertIsNone(msg1.deleted_at)
        self.assertIsNone(msg2.deleted_at)

    def test_ignores_already_soft_deleted(self):
        """Already soft-deleted messages are not considered."""
        deleted_msg = self._make_msg(100, deleted_at=timezone.now())
        active_msg = self._make_msg(200)

        # Server has only 200 — UID 100 is gone but already soft-deleted
        conn = self._mock_conn([200])
        _reconcile(conn, self.folder)

        deleted_msg.refresh_from_db()
        active_msg.refresh_from_db()
        self.assertIsNotNone(deleted_msg.deleted_at)
        self.assertIsNone(active_msg.deleted_at)

    def test_no_local_messages_returns_early(self):
        """No-op when there are no local active messages."""
        conn = self._mock_conn([100])
        _reconcile(conn, self.folder)
        # Should not have fetched any flag
        self.assertEqual(conn.server.named("fetch"), [])

    def test_the_final_update_still_excludes_already_deleted_rows(self):
        """The pk-scoped UPDATE (split off to capture gone_pks first) must
        keep the deleted_at IS NULL guard the original single-query form
        had. Without it, a message soft-deleted by something else between
        the pk-capture SELECT and this UPDATE would be re-stamped with a
        newer deleted_at and double-counted in the reconciled total."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._make_msg(100)
        conn = self._mock_conn([])

        with CaptureQueriesContext(connection) as ctx:
            _reconcile(conn, self.folder)

        update_queries = [
            q
            for q in ctx.captured_queries
            if q["sql"].startswith("UPDATE") and "mail_mailmessage" in q["sql"]
        ]
        self.assertEqual(len(update_queries), 1)
        self.assertIn("IS NULL", update_queries[0]["sql"].upper())

    def test_soft_delete_marks_the_messages_notification_read(self):
        """A message vanished from another IMAP client never CASCADEs its
        Notification row (this is a soft delete): the notification must be
        marked read directly, or it stays unread forever - no rendered page
        can ever list a deleted message for mark_sources_read to catch."""
        msg = self._make_msg(100)
        notif = Notification.objects.create(
            recipient=self.user,
            origin="mail",
            icon="",
            title="Hi",
            mail_message=msg,
        )

        conn = self._mock_conn([])
        _reconcile(conn, self.folder)

        notif.refresh_from_db()
        self.assertIsNotNone(notif.read_at)


class ReconcileFlagTests(ReconcileFolderMixin, TestCase):
    """Tests for bulk flag synchronization during reconciliation."""

    def test_marks_read(self):
        """Unread local message marked read when server has \\Seen."""
        self._make_msg(100, is_read=False)

        conn = self._mock_conn([100], flags={100: r"\Seen"})
        _reconcile(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertTrue(msg.is_read)

    def test_marks_unread(self):
        """Read local message marked unread when server lacks \\Seen."""
        self._make_msg(100, is_read=True)

        conn = self._mock_conn([100], flags={100: ""})
        _reconcile(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertFalse(msg.is_read)

    def test_marks_starred(self):
        """Unstarred local message marked starred when server has \\Flagged."""
        self._make_msg(100, is_starred=False)

        conn = self._mock_conn([100], flags={100: r"\Flagged"})
        _reconcile(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertTrue(msg.is_starred)

    def test_marks_unstarred(self):
        """Starred local message unmarked when server lacks \\Flagged."""
        self._make_msg(100, is_starred=True)

        conn = self._mock_conn([100], flags={100: ""})
        _reconcile(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertFalse(msg.is_starred)

    def test_marks_answered(self):
        """A message answered from another client comes in answered."""
        self._make_msg(100)

        conn = self._mock_conn([100], flags={100: r"\Answered"})
        _reconcile(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertTrue(msg.is_answered)

    def test_marks_unanswered(self):
        """Answered local message unmarked when server lacks \\Answered."""
        self._make_msg(100, is_answered=True)

        conn = self._mock_conn([100], flags={100: r"\Seen"})
        _reconcile(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertFalse(msg.is_answered)
        self.assertTrue(msg.is_read)

    def test_combined_flags(self):
        """Both \\Seen and \\Flagged are applied together."""
        self._make_msg(100, is_read=False, is_starred=False)

        conn = self._mock_conn([100], flags={100: r"\Seen \Flagged"})
        _reconcile(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertTrue(msg.is_read)
        self.assertTrue(msg.is_starred)

    def test_mixed_flags_across_messages(self):
        """Different flags on different messages are applied correctly."""
        self._make_msg(100, is_read=False, is_starred=False)
        self._make_msg(200, is_read=True, is_starred=True)

        conn = self._mock_conn(
            [100, 200],
            flags={
                100: r"\Seen \Flagged",
                200: "",
            },
        )
        _reconcile(conn, self.folder)

        msg100 = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        msg200 = MailMessage.objects.get(imap_uid=200, folder=self.folder)
        self.assertTrue(msg100.is_read)
        self.assertTrue(msg100.is_starred)
        self.assertFalse(msg200.is_read)
        self.assertFalse(msg200.is_starred)

    def test_label_unread_count_recomputed_when_marking_read(self):
        """Marking a message read during sync must refresh MailLabel.unread_count
        for any label attached to that message - otherwise the sidebar badge
        stays stale until the next user-side toggle."""
        from workspace.mail.models import MailLabel, MailMessageLabel

        msg = self._make_msg(100, is_read=False)
        label = MailLabel.objects.create(
            account=self.account,
            name="Important",
            unread_count=1,
        )
        MailMessageLabel.objects.create(message=msg, label=label)

        conn = self._mock_conn([100], flags={100: r"\Seen"})
        _reconcile(conn, self.folder)

        label.refresh_from_db()
        self.assertEqual(label.unread_count, 0)

    def test_label_unread_count_recomputed_when_marking_unread(self):
        """The reverse direction: server marked a message unread, label
        unread_count should reflect the increment."""
        from workspace.mail.models import MailLabel, MailMessageLabel

        msg = self._make_msg(100, is_read=True)
        label = MailLabel.objects.create(
            account=self.account,
            name="Important",
            unread_count=0,
        )
        MailMessageLabel.objects.create(message=msg, label=label)

        conn = self._mock_conn([100], flags={100: ""})
        _reconcile(conn, self.folder)

        label.refresh_from_db()
        self.assertEqual(label.unread_count, 1)

    def test_externally_read_message_settles_its_notification(self):
        """A message read from another IMAP client never renders a page here,
        so reconciliation itself must mark its notification read - otherwise
        the badge keeps counting a message the user has already seen."""
        msg = self._make_msg(100, is_read=False)
        notif = Notification.objects.create(
            recipient=self.user,
            origin="mail",
            icon="mail",
            title="New email",
            mail_message=msg,
        )

        conn = self._mock_conn([100], flags={100: r"\Seen"})
        _reconcile(conn, self.folder)

        notif.refresh_from_db()
        self.assertIsNotNone(notif.read_at)

    def test_marking_unread_does_not_touch_notifications(self):
        """Only the read direction settles: a message flipped back to unread
        on the server must not mark anything read."""
        msg = self._make_msg(100, is_read=True)
        notif = Notification.objects.create(
            recipient=self.user,
            origin="mail",
            icon="mail",
            title="New email",
            mail_message=msg,
        )

        conn = self._mock_conn([100], flags={100: ""})
        _reconcile(conn, self.folder)

        notif.refresh_from_db()
        self.assertIsNone(notif.read_at)

    def test_flag_fetch_failure_is_raised_and_changes_nothing(self):
        """A refused FETCH is an error, not a folder without flags: taken for
        one, it would mark every message unread and unstarred."""
        self._make_msg(100, is_read=True, is_starred=True)
        conn = self._mock_conn([100], flags={100: r"\Seen \Flagged"})
        conn.server.fail("fetch")

        with self.assertRaises(IMAPClientError):
            _reconcile(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertTrue(msg.is_read)
        self.assertTrue(msg.is_starred)

    def test_only_the_messages_the_server_answers_for_change(self):
        """With CHANGEDSINCE the server leaves out the messages whose flags
        did not move: silence about a message is not "no flags"."""
        self._make_msg(100, is_read=True, is_starred=True)
        self._make_msg(200, is_read=False)
        conn = self._mock_conn(
            [100, 200], flags={100: r"\Seen \Flagged", 200: r"\Seen"}
        )
        before = conn.selected.highestmodseq
        conn.server.set_flags(self.folder.name, 200, r"\Seen", r"\Flagged")

        _reconcile(conn, self.folder, changed_since=before)

        # One SEARCH names what moved, and only that is read.
        self.assertEqual(
            conn.server.named("search")[-1],
            ("search", ["UID", "100:*", "MODSEQ", before + 1]),
        )
        (fetch,) = conn.server.named("fetch")
        self.assertEqual(fetch[1], [200])
        unchanged = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertTrue(unchanged.is_read)
        self.assertTrue(unchanged.is_starred)
        changed = MailMessage.objects.get(imap_uid=200, folder=self.folder)
        self.assertTrue(changed.is_read)
        self.assertTrue(changed.is_starred)


class SyncReconciliationIntegrationTests(ReconcileFolderMixin, TestCase):
    """Tests that sync_folder_messages always runs reconciliation."""

    def test_reconciliation_runs_with_no_new_messages(self):
        """Reconciliation runs even when there are no new UIDs to fetch."""
        with fake_imap() as server:
            # Message in DB but gone from server
            msg = self._make_msg(400)
            self.folder.last_sync_uid = 500
            self.folder.uid_validity = server.mailbox("INBOX").uidvalidity
            self.folder.save()

            sync_folder_messages(self.account, self.folder)

        # Reconciliation should have soft-deleted the message
        msg.refresh_from_db()
        self.assertIsNotNone(msg.deleted_at)

    def test_last_sync_uid_advances_for_already_present_messages(self):
        """When FETCH returns UIDs that _parse_message recognises as already
        in DB (returns None), max_uid must still advance so last_sync_uid
        moves forward. Otherwise after a crash mid-sync we'd re-FETCH the
        same UIDs every time."""
        with fake_imap() as server:
            server.deliver("INBOX", uid=42)
            # Message UID 42 is already in DB (simulates a crash after
            # persist but before last_sync_uid update).
            self._make_msg(42)
            self.folder.last_sync_uid = 41
            self.folder.uid_validity = server.mailbox("INBOX").uidvalidity
            self.folder.save()

            sync_folder_messages(self.account, self.folder)

        self.folder.refresh_from_db()
        self.assertEqual(self.folder.last_sync_uid, 42)
        self.assertEqual(MailMessage.objects.filter(folder=self.folder).count(), 1)

    def test_a_failed_search_deletes_nothing(self):
        """No answer about what the server holds is not an empty mailbox."""
        with fake_imap() as server:
            server.deliver("INBOX", uid=100)
            self._make_msg(100)
            self.folder.last_sync_uid = 100
            self.folder.save()
            server.fail("search")

            with self.assertRaises(IMAPClientError):
                sync_folder_messages(self.account, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertIsNone(msg.deleted_at)


class ReconcileBeyondTheParameterLimitTests(ReconcileFolderMixin, TestCase):
    """A folder holding more messages than SQLite binds in one statement.

    The limit is lowered for the pass, so a hundred messages stand in for the
    32766 it takes in production.
    """

    PARAMETER_LIMIT = 50
    MESSAGE_COUNT = 120

    def _fill_folder(self, **fields):
        MailMessage.objects.bulk_create(
            MailMessage(
                account=self.account, folder=self.folder, imap_uid=uid, **fields
            )
            for uid in self._uids()
        )

    def _uids(self):
        return range(1, self.MESSAGE_COUNT + 1)

    def _reconcile(self, conn):
        with sqlite_parameter_limit(self.PARAMETER_LIMIT):
            _reconcile(conn, self.folder)

    def _notify(self, message):
        return Notification.objects.create(
            recipient=self.user,
            origin="mail",
            title="New mail",
            mail_message=message,
        )

    def test_a_pass_that_changes_nothing_goes_through(self):
        self._fill_folder(is_read=True)
        flags = dict.fromkeys(self._uids(), r"\Seen")

        self._reconcile(self._mock_conn(self._uids(), flags=flags))

        messages = MailMessage.objects.filter(folder=self.folder)
        self.assertEqual(messages.filter(is_read=True).count(), self.MESSAGE_COUNT)
        self.assertFalse(messages.filter(deleted_at__isnull=False).exists())

    def test_every_message_read_from_another_client(self):
        from workspace.mail.models import MailLabel, MailMessageLabel

        self._fill_folder(is_read=False)
        messages = list(MailMessage.objects.filter(folder=self.folder))
        label = MailLabel.objects.create(
            account=self.account, name="Important", unread_count=self.MESSAGE_COUNT
        )
        MailMessageLabel.objects.bulk_create(
            MailMessageLabel(message=message, label=label) for message in messages
        )
        notification = self._notify(messages[0])
        flags = dict.fromkeys(self._uids(), r"\Seen")

        self._reconcile(self._mock_conn(self._uids(), flags=flags))

        unread = MailMessage.objects.filter(folder=self.folder, is_read=False)
        self.assertFalse(unread.exists())
        label.refresh_from_db()
        self.assertEqual(label.unread_count, 0)
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)

    def test_every_flag_flipped_the_other_way(self):
        self._fill_folder(is_read=True, is_starred=True)

        self._reconcile(self._mock_conn(self._uids()))

        messages = MailMessage.objects.filter(folder=self.folder)
        self.assertFalse(messages.filter(is_read=True).exists())
        self.assertFalse(messages.filter(is_starred=True).exists())

    def test_every_message_starred_from_another_client(self):
        self._fill_folder(is_read=True)
        flags = dict.fromkeys(self._uids(), r"\Seen \Flagged")

        self._reconcile(self._mock_conn(self._uids(), flags=flags))

        starred = MailMessage.objects.filter(folder=self.folder, is_starred=True)
        self.assertEqual(starred.count(), self.MESSAGE_COUNT)

    def test_a_folder_emptied_from_another_client(self):
        self._fill_folder()
        kept = self._make_msg(self.MESSAGE_COUNT + 1)
        gone = MailMessage.objects.get(folder=self.folder, imap_uid=1)
        notification = self._notify(gone)

        self._reconcile(self._mock_conn([kept.imap_uid]))

        live = MailMessage.objects.filter(folder=self.folder, deleted_at__isnull=True)
        self.assertEqual(list(live), [kept])
        notification.refresh_from_db()
        self.assertIsNotNone(notification.read_at)
