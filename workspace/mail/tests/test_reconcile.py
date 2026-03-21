from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap import _reconcile_folder

User = get_user_model()


class ReconcileFolderMixin:
    """Common setup for reconciliation tests."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='reconcileuser', email='reconcile@test.com', password='pass',
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@example.com',
            imap_host='imap.example.com',
            imap_use_ssl=True,
            smtp_host='smtp.example.com',
            username='user@example.com',
        )
        self.account.set_password('secret')
        self.account.save()
        self.folder = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            display_name='Inbox',
            folder_type='inbox',
        )

    def _make_msg(self, uid, is_read=False, is_starred=False, deleted_at=None):
        return MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=uid,
            is_read=is_read,
            is_starred=is_starred,
            deleted_at=deleted_at,
        )

    def _mock_conn(self, remote_uids, flags=None):
        """Build a mock IMAP connection.

        ``remote_uids`` — iterable of ints returned by UID SEARCH ALL.
        ``flags``       — optional dict {uid: flags_str} for UID FETCH FLAGS.
                          Defaults to empty flags for every remote uid.
        """
        conn = MagicMock()

        # UID SEARCH ALL
        uid_bytes = b' '.join(str(u).encode() for u in remote_uids)
        conn.uid.return_value = ('OK', [uid_bytes])

        if flags is None:
            flags = {u: '' for u in remote_uids}

        def uid_side_effect(cmd, *args):
            if cmd == 'SEARCH':
                return ('OK', [uid_bytes])
            if cmd == 'FETCH':
                responses = []
                for uid, flag_str in flags.items():
                    responses.append(
                        (f'* {uid} FETCH (UID {uid} FLAGS ({flag_str}))'.encode(), b'')
                    )
                return ('OK', responses)
            return ('OK', [b''])

        conn.uid.side_effect = uid_side_effect
        return conn


class ReconcileSoftDeleteTests(ReconcileFolderMixin, TestCase):
    """Tests for soft-deleting messages whose UIDs are gone from server."""

    def test_soft_deletes_gone_uids(self):
        """Messages whose UIDs no longer exist on server are soft-deleted."""
        msg1 = self._make_msg(100)
        msg2 = self._make_msg(200)
        msg3 = self._make_msg(300)

        # Server only has UID 200 — 100 and 300 are gone
        conn = self._mock_conn([200])
        _reconcile_folder(conn, self.folder)

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
        _reconcile_folder(conn, self.folder)

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
        _reconcile_folder(conn, self.folder)

        deleted_msg.refresh_from_db()
        active_msg.refresh_from_db()
        self.assertIsNotNone(deleted_msg.deleted_at)
        self.assertIsNone(active_msg.deleted_at)

    def test_no_local_messages_returns_early(self):
        """No-op when there are no local active messages."""
        conn = MagicMock()
        _reconcile_folder(conn, self.folder)
        # Should not have called UID SEARCH at all
        conn.uid.assert_not_called()

    def test_search_failure_returns_early(self):
        """No deletions if UID SEARCH ALL fails."""
        self._make_msg(100)

        conn = MagicMock()
        conn.uid.return_value = ('NO', [b''])
        _reconcile_folder(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertIsNone(msg.deleted_at)


class ReconcileFlagTests(ReconcileFolderMixin, TestCase):
    """Tests for bulk flag synchronization during reconciliation."""

    def test_marks_read(self):
        """Unread local message marked read when server has \\Seen."""
        self._make_msg(100, is_read=False)

        conn = self._mock_conn([100], flags={100: r'\Seen'})
        _reconcile_folder(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertTrue(msg.is_read)

    def test_marks_unread(self):
        """Read local message marked unread when server lacks \\Seen."""
        self._make_msg(100, is_read=True)

        conn = self._mock_conn([100], flags={100: ''})
        _reconcile_folder(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertFalse(msg.is_read)

    def test_marks_starred(self):
        """Unstarred local message marked starred when server has \\Flagged."""
        self._make_msg(100, is_starred=False)

        conn = self._mock_conn([100], flags={100: r'\Flagged'})
        _reconcile_folder(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertTrue(msg.is_starred)

    def test_marks_unstarred(self):
        """Starred local message unmarked when server lacks \\Flagged."""
        self._make_msg(100, is_starred=True)

        conn = self._mock_conn([100], flags={100: ''})
        _reconcile_folder(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertFalse(msg.is_starred)

    def test_combined_flags(self):
        """Both \\Seen and \\Flagged are applied together."""
        self._make_msg(100, is_read=False, is_starred=False)

        conn = self._mock_conn([100], flags={100: r'\Seen \Flagged'})
        _reconcile_folder(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertTrue(msg.is_read)
        self.assertTrue(msg.is_starred)

    def test_mixed_flags_across_messages(self):
        """Different flags on different messages are applied correctly."""
        self._make_msg(100, is_read=False, is_starred=False)
        self._make_msg(200, is_read=True, is_starred=True)

        conn = self._mock_conn([100, 200], flags={
            100: r'\Seen \Flagged',
            200: '',
        })
        _reconcile_folder(conn, self.folder)

        msg100 = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        msg200 = MailMessage.objects.get(imap_uid=200, folder=self.folder)
        self.assertTrue(msg100.is_read)
        self.assertTrue(msg100.is_starred)
        self.assertFalse(msg200.is_read)
        self.assertFalse(msg200.is_starred)

    def test_flag_fetch_failure_skips_update(self):
        """Flag update is skipped when UID FETCH fails."""
        self._make_msg(100, is_read=False)

        conn = MagicMock()
        # SEARCH succeeds, FETCH fails
        def uid_side_effect(cmd, *args):
            if cmd == 'SEARCH':
                return ('OK', [b'100'])
            if cmd == 'FETCH':
                return ('NO', [b''])
            return ('OK', [b''])

        conn.uid.side_effect = uid_side_effect
        _reconcile_folder(conn, self.folder)

        msg = MailMessage.objects.get(imap_uid=100, folder=self.folder)
        self.assertFalse(msg.is_read)


class SyncReconciliationIntegrationTests(ReconcileFolderMixin, TestCase):
    """Tests that sync_folder_messages always runs reconciliation."""

    @patch('workspace.mail.services.imap.connect_imap')
    def test_reconciliation_runs_with_no_new_messages(self, mock_connect):
        """Reconciliation runs even when there are no new UIDs to fetch."""
        self.folder.last_sync_uid = 500
        self.folder.uid_validity = 12345
        self.folder.save()

        # Message in DB but gone from server
        msg = self._make_msg(400)

        conn = MagicMock()
        # SELECT OK
        conn.select.return_value = ('OK', [b'1'])
        # Make _get_uidvalidity return same value (no reset)
        conn.untagged_responses = {'OK': [b'[UIDVALIDITY 12345]']}

        call_count = 0

        def uid_side_effect(cmd, *args):
            nonlocal call_count
            call_count += 1
            if cmd == 'SEARCH':
                search_arg = args[1] if len(args) > 1 else ''
                if 'UID' in str(search_arg) and 'ALL' not in str(search_arg):
                    # Incremental search — no new UIDs
                    return ('OK', [b''])
                else:
                    # Reconciliation SEARCH ALL — server is empty
                    return ('OK', [b''])
            if cmd == 'FETCH':
                return ('OK', [])
            return ('OK', [b''])

        conn.uid.side_effect = uid_side_effect
        conn.logout.return_value = ('OK', [b'BYE'])
        mock_connect.return_value = conn

        from workspace.mail.services.imap import sync_folder_messages
        sync_folder_messages(self.account, self.folder)

        # Reconciliation should have soft-deleted the message
        msg.refresh_from_db()
        self.assertIsNotNone(msg.deleted_at)
