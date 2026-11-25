from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from imapclient.exceptions import IMAPClientError

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_sync import FETCH_BATCH_SIZE, sync_folder_messages
from workspace.mail.tests.imap_fake import FakeImapClient, fake_imap, raw_message
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

User = get_user_model()


def _message_of(size):
    """A message weighing exactly `size` bytes."""
    padding = size - len(raw_message(body=""))
    return raw_message(body="x" * padding)


def _message_fetches(server):
    """The UIDs of each FETCH that downloads messages, in order."""
    return [
        command[1]
        for command in server.named("fetch")
        if command[2] == ["FLAGS", "BODY.PEEK[]"]
    ]


def refused_download_of(uid):
    """Have the server refuse the FETCH that downloads message `uid`."""
    answer = FakeImapClient.fetch

    def fetch(client, messages, data, modifiers=None):
        if "BODY.PEEK[]" in data and uid in messages:
            raise IMAPClientError("fetch failed: NO")
        return answer(client, messages, data, modifiers)

    return patch.object(FakeImapClient, "fetch", fetch)


class SyncFetchBatchingMixin:
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

    def _stored_uids(self):
        return sorted(
            MailMessage.objects.filter(folder=self.folder).values_list(
                "imap_uid", flat=True
            )
        )


class SyncFetchBatchingTests(SyncFetchBatchingMixin, TestCase):
    """The UID FETCH loop must chunk uid_list into FETCH_BATCH_SIZE-sized
    groups. Pins the itertools.batched refactor: a regression in the
    chunk boundary would either drop the trailing UIDs or send one giant
    FETCH the server may reject."""

    def test_fetch_is_chunked_by_batch_size(self):
        with fake_imap() as server:
            # One more than a full batch -> exactly two FETCH calls, the
            # second carrying just the leftover UID.
            uids = [server.deliver("INBOX") for _ in range(FETCH_BATCH_SIZE + 1)]

            sync_folder_messages(self.account, self.folder)

        self.assertEqual(
            _message_fetches(server), [uids[:FETCH_BATCH_SIZE], [uids[-1]]]
        )
        self.assertEqual(self._stored_uids(), uids)

    def test_exact_batch_size_is_single_fetch(self):
        with fake_imap() as server:
            # Exactly one full batch -> a single FETCH, no empty trailing call.
            uids = [server.deliver("INBOX") for _ in range(FETCH_BATCH_SIZE)]

            sync_folder_messages(self.account, self.folder)

        self.assertEqual(_message_fetches(server), [uids])
        self.assertEqual(self._stored_uids(), uids)

    def test_a_single_new_message_is_fetched_without_asking_its_size(self):
        with fake_imap() as server:
            uid = server.deliver("INBOX")

            sync_folder_messages(self.account, self.folder)

        self.assertEqual(
            [command[2] for command in server.named("fetch")],
            [["FLAGS", "BODY.PEEK[]"]],
        )
        self.assertEqual(self._stored_uids(), [uid])


class SyncFetchByteBudgetTests(SyncFetchBatchingMixin, TestCase):
    """Regression for #1763: a FETCH answer is held in memory whole, so a
    batch is bounded by the bytes it brings back and not only by its count."""

    BUDGET = 1000

    def _sync(self, sizes, batch_size=FETCH_BATCH_SIZE):
        with (
            fake_imap() as server,
            patch("workspace.mail.services.imap_sync.FETCH_BATCH_BYTES", self.BUDGET),
            patch("workspace.mail.services.imap_sync.FETCH_BATCH_SIZE", batch_size),
        ):
            for size in sizes:
                server.deliver("INBOX", raw=_message_of(size))
            sync_folder_messages(self.account, self.folder)
        return server

    def test_no_batch_brings_back_more_than_the_budget(self):
        # 300 + 700 fills a batch to the byte: the limit is inclusive.
        sizes = [400, 400, 300, 700, 200, 900]

        server = self._sync(sizes)

        batches = _message_fetches(server)
        self.assertEqual(batches, [[1, 2], [3, 4], [5], [6]])
        weights = [sum(sizes[uid - 1] for uid in batch) for batch in batches]
        self.assertLessEqual(max(weights), self.BUDGET)
        self.assertEqual(self._stored_uids(), [1, 2, 3, 4, 5, 6])

    def test_a_message_over_the_budget_is_fetched_alone(self):
        sizes = [400, 3000, 400, 400]

        server = self._sync(sizes)

        self.assertEqual(_message_fetches(server), [[1], [2], [3, 4]])
        self.assertEqual(self._stored_uids(), [1, 2, 3, 4])
        oversized = MailMessage.objects.get(folder=self.folder, imap_uid=2)
        self.assertGreater(len(oversized.body_text), self.BUDGET)

    def test_the_message_count_still_closes_a_batch_under_the_budget(self):
        sizes = [200, 200, 200, 200, 600, 600, 200]

        server = self._sync(sizes, batch_size=3)

        # The first batch stops at three messages with 400 bytes to spare,
        # the second at two because a third would not fit.
        self.assertEqual(_message_fetches(server), [[1, 2, 3], [4, 5], [6, 7]])
        self.assertEqual(self._stored_uids(), [1, 2, 3, 4, 5, 6, 7])


class SyncCursorTests(SyncFetchBatchingMixin, TestCase):
    """A pass that dies keeps what it imported, and what it owes it."""

    def setUp(self):
        super().setUp()
        set_setting(self.user, "mail", "notify_mode", "all")
        self.addCleanup(cache.clear)
        self.enterContext(
            patch("workspace.notifications.tasks.send_push_notification.delay")
        )
        self.enterContext(
            patch("workspace.mail.services.imap_sync.FETCH_BATCH_SIZE", 2)
        )

    def _notified_uids(self):
        return sorted(
            Notification.objects.filter(mail_message__folder=self.folder).values_list(
                "mail_message__imap_uid", flat=True
            )
        )

    def test_a_pass_failing_on_its_second_batch_keeps_the_first(self):
        """The cursor is saved after each batch, so the next pass resumes at
        the batch this one died on."""
        with fake_imap() as server:
            first = server.deliver("INBOX")
            sync_folder_messages(self.account, self.folder)
            uids = [server.deliver("INBOX") for _ in range(5)]

            with refused_download_of(uids[2]), self.assertRaises(IMAPClientError):
                sync_folder_messages(self.account, self.folder)

            stored = MailFolder.objects.get(pk=self.folder.pk)
            self.assertEqual(stored.last_sync_uid, uids[1])
            self.assertEqual(self._stored_uids(), [first, *uids[:2]])
            first_passes = len(_message_fetches(server))

            sync_folder_messages(self.account, self.folder)

        self.assertEqual(
            _message_fetches(server)[first_passes:], [uids[2:4], [uids[4]]]
        )
        self.assertEqual(self._stored_uids(), [first, *uids])

    def test_what_a_failing_pass_imported_still_gets_its_rules_and_notification(self):
        """The next pass finds those messages stored, past the cursor, and
        has no reason to look at them again: this pass is their only chance."""
        with fake_imap() as server:
            server.deliver("INBOX")
            sync_folder_messages(self.account, self.folder)
            uids = [server.deliver("INBOX") for _ in range(5)]

            with (
                refused_download_of(uids[2]),
                patch(
                    "workspace.mail.services.rules.engine.run_rules_for_messages"
                ) as rules,
                self.assertRaises(IMAPClientError),
            ):
                sync_folder_messages(self.account, self.folder)

            imported = MailMessage.objects.filter(
                folder=self.folder, imap_uid__in=uids[:2]
            )
            rules.assert_called_once_with(
                self.account, [str(message.uuid) for message in imported]
            )
            self.assertEqual(self._notified_uids(), uids[:2])

            sync_folder_messages(self.account, self.folder)

        self.assertEqual(self._notified_uids(), uids)

    def test_an_interrupted_first_pass_is_still_a_first_pass(self):
        """A first pass imports mail that can be weeks old, in silence.
        Resumed from a saved cursor it would be an ordinary pass, and would
        notify for the rest of it."""
        with fake_imap() as server:
            uids = [server.deliver("INBOX") for _ in range(5)]

            with refused_download_of(uids[2]), self.assertRaises(IMAPClientError):
                sync_folder_messages(self.account, self.folder)

            stored = MailFolder.objects.get(pk=self.folder.pk)
            self.assertEqual(stored.last_sync_uid, 0)
            self.assertEqual(self._stored_uids(), uids[:2])

            sync_folder_messages(self.account, self.folder)

        self.assertEqual(self._stored_uids(), uids)
        self.assertEqual(self._notified_uids(), [])
        self.assertEqual(
            MailFolder.objects.get(pk=self.folder.pk).last_sync_uid, uids[-1]
        )


class SyncFolderCountsTests(SyncFetchBatchingMixin, TestCase):
    def test_a_pass_stores_the_folder_counters_and_its_cursor(self):
        with fake_imap() as server:
            server.deliver("INBOX", flags=[r"\Seen"])
            last = server.deliver("INBOX")

            sync_folder_messages(self.account, self.folder)

        stored = MailFolder.objects.get(pk=self.folder.pk)
        self.assertEqual(
            (stored.message_count, stored.unread_count, stored.last_sync_uid),
            (2, 1, last),
        )
