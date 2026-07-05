from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder
from workspace.mail.services.imap_sync import FETCH_BATCH_SIZE

User = get_user_model()


class SyncFetchBatchingTests(TestCase):
    """The UID FETCH loop must chunk uid_list into FETCH_BATCH_SIZE-sized
    groups. Pins the itertools.batched refactor: a regression in the
    chunk boundary would either drop the trailing UIDs or send one giant
    FETCH the server may reject."""

    def setUp(self):
        self.user = User.objects.create_user(username="fb", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="fb@x.com",
            imap_host="x",
            smtp_host="x",
            username="fb@x.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            folder_type="inbox",
            last_sync_uid=0,
        )

    def _fetch_uid_sets(self, conn):
        """Return the uid_set string of each UID FETCH call, in order."""
        return [
            call.args[1]
            for call in conn.uid.call_args_list
            if call.args and call.args[0] == "FETCH"
        ]

    @patch("workspace.mail.services.imap_sync._reconcile_folder")
    @patch("workspace.mail.services.imap_sync.connect_imap")
    def test_fetch_is_chunked_by_batch_size(self, mock_connect, _mock_recon):
        from workspace.mail.services.imap_sync import sync_folder_messages

        # One more than a full batch -> exactly two FETCH calls, the second
        # carrying just the leftover UID.
        uids = list(range(1, FETCH_BATCH_SIZE + 2))
        search_bytes = b" ".join(str(u).encode() for u in uids)

        conn = MagicMock()
        conn.select.return_value = ("OK", [b"1"])
        # 1st uid() call: UID SEARCH ALL. Following calls: one UID FETCH per
        # batch, each returning no message parts (batching is what we assert).
        conn.uid.side_effect = [
            ("OK", [search_bytes]),
            ("OK", []),
            ("OK", []),
        ]
        mock_connect.return_value = conn

        sync_folder_messages(self.account, self.folder)

        fetch_sets = self._fetch_uid_sets(conn)
        self.assertEqual(len(fetch_sets), 2)
        # First batch: the first FETCH_BATCH_SIZE UIDs.
        self.assertEqual(
            fetch_sets[0], ",".join(str(u) for u in uids[:FETCH_BATCH_SIZE])
        )
        # Second batch: the single leftover UID.
        self.assertEqual(fetch_sets[1], str(uids[-1]))

    @patch("workspace.mail.services.imap_sync._reconcile_folder")
    @patch("workspace.mail.services.imap_sync.connect_imap")
    def test_exact_batch_size_is_single_fetch(self, mock_connect, _mock_recon):
        from workspace.mail.services.imap_sync import sync_folder_messages

        # Exactly one full batch -> a single FETCH, no empty trailing call.
        uids = list(range(1, FETCH_BATCH_SIZE + 1))
        search_bytes = b" ".join(str(u).encode() for u in uids)

        conn = MagicMock()
        conn.select.return_value = ("OK", [b"1"])
        conn.uid.side_effect = [
            ("OK", [search_bytes]),
            ("OK", []),
        ]
        mock_connect.return_value = conn

        sync_folder_messages(self.account, self.folder)

        fetch_sets = self._fetch_uid_sets(conn)
        self.assertEqual(len(fetch_sets), 1)
        self.assertEqual(
            fetch_sets[0], ",".join(str(u) for u in uids)
        )
