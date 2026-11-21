"""The shared per-message triage service.

The rules here used to live inside the mail views; the AI tools now go
through the same functions, so each rule is pinned once, at the level both
callers share.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models import QuerySet
from django.test import TestCase
from imapclient.exceptions import IMAPClientError

from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailLabel,
    MailMessage,
    MailMessageLabel,
)
from workspace.mail.services.triage import (
    flag_operations,
    mark_folder_read,
    move_to_folder,
    set_flag,
    set_label,
)
from workspace.mail.tests.imap_fake import fake_imap

User = get_user_model()


class TriageServiceTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="triage", password="pw")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="me@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="me@example.com",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
            unread_count=99,
        )
        self.archive = MailFolder.objects.create(
            account=self.account,
            name="Archive",
            display_name="Archive",
            folder_type="archive",
            message_count=42,
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=1,
            subject="Invoice",
            is_read=False,
        )


class SetFlagTests(TriageServiceTestCase):
    def test_every_flag_name_maps_to_a_call_and_a_column(self):
        self.assertEqual(
            set(flag_operations()), {"read", "unread", "starred", "unstarred"}
        )

    def test_a_synced_flag_reports_true_and_recomputes_counts(self):
        with patch("workspace.mail.services.imap_messages.mark_read") as mark:
            self.assertTrue(set_flag(self.message, "read"))
        self.message.refresh_from_db()
        self.inbox.refresh_from_db()
        self.assertTrue(self.message.is_read)
        self.assertEqual(self.inbox.unread_count, 0)
        mark.assert_called_once_with(self.account, self.message)

    def test_a_refused_flag_reports_false_but_still_applies_locally(self):
        with patch(
            "workspace.mail.services.imap_messages.star_message",
            side_effect=OSError("connection reset"),
        ):
            self.assertFalse(set_flag(self.message, "starred"))
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_starred)

    def test_read_flags_propagate_to_the_label_counters(self):
        label = MailLabel.objects.get(account=self.account, name="Urgent")
        MailMessageLabel.objects.create(message=self.message, label=label)
        MailLabel.objects.filter(pk=label.pk).update(unread_count=7)
        with patch("workspace.mail.services.imap_messages.mark_read"):
            set_flag(self.message, "read")
        label.refresh_from_db()
        self.assertEqual(label.unread_count, 0)

    def test_a_star_leaves_the_label_counters_alone(self):
        label = MailLabel.objects.get(account=self.account, name="Urgent")
        MailMessageLabel.objects.create(message=self.message, label=label)
        MailLabel.objects.filter(pk=label.pk).update(unread_count=7)
        with patch("workspace.mail.services.imap_messages.star_message"):
            set_flag(self.message, "starred")
        label.refresh_from_db()
        self.assertEqual(label.unread_count, 7)


class SetFlagOnTheServerTests(TriageServiceTestCase):
    """`set_flag` down to the mailbox it writes to."""

    def setUp(self):
        super().setUp()
        self.server = self.enterContext(fake_imap())
        self.server.deliver("INBOX", uid=self.message.imap_uid, subject="Invoice")

    def _server_flags(self):
        return self.server.flags("INBOX", self.message.imap_uid)

    def test_each_flag_reaches_the_mailbox(self):
        self.assertTrue(set_flag(self.message, "read"))
        self.assertEqual(self._server_flags(), {r"\Seen"})
        self.assertTrue(set_flag(self.message, "starred"))
        self.assertEqual(self._server_flags(), {r"\Seen", r"\Flagged"})
        self.assertTrue(set_flag(self.message, "unread"))
        self.assertEqual(self._server_flags(), {r"\Flagged"})
        self.assertTrue(set_flag(self.message, "unstarred"))
        self.assertEqual(self._server_flags(), set())

    def test_a_store_answered_no_reports_false(self):
        """Issue #1740: the answer to the STORE was not read, so a flag the
        server refused was reported as synced."""
        self.server.fail("add_flags")

        self.assertFalse(set_flag(self.message, "read"))

        self.assertEqual(self._server_flags(), set())
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_read)

    def test_a_removal_answered_no_reports_false(self):
        self.server.set_flags("INBOX", self.message.imap_uid, r"\Flagged")
        self.server.fail("remove_flags")

        self.assertFalse(set_flag(self.message, "unstarred"))

        self.assertEqual(self._server_flags(), {r"\Flagged"})

    def test_a_store_answered_no_raises_from_the_imap_service(self):
        from workspace.mail.services.imap_messages import mark_read, unstar_message

        self.server.fail("add_flags")
        self.server.fail("remove_flags")

        with self.assertRaises(IMAPClientError):
            mark_read(self.account, self.message)
        with self.assertRaises(IMAPClientError):
            unstar_message(self.account, self.message)

    def test_a_refused_select_reports_false(self):
        self.server.fail("select")

        self.assertFalse(set_flag(self.message, "starred"))
        self.assertEqual(self.server.named("add_flags"), [])

    def test_a_refused_flag_is_put_right_by_the_next_sync(self):
        """The row says read and the mailbox does not, without the mailbox
        having changed: the sync must not skip it as untouched."""
        from workspace.mail.services.imap_sync import sync_folder_messages

        MailFolder.objects.filter(pk=self.inbox.pk).update(
            uid_validity=self.server.mailbox("INBOX").uidvalidity,
            last_sync_uid=self.message.imap_uid,
        )
        self.inbox.refresh_from_db()
        sync_folder_messages(self.account, self.inbox)
        self.inbox.refresh_from_db()
        self.assertIsNotNone(self.inbox.remote_state)
        self.message.refresh_from_db()

        self.server.fail("add_flags")
        self.assertFalse(set_flag(self.message, "read"))
        self.inbox.refresh_from_db()
        self.assertIsNone(self.inbox.remote_state)

        self.server.recover("add_flags")
        sync_folder_messages(self.account, self.inbox)
        self.message.refresh_from_db()
        self.assertFalse(self.message.is_read)

    def test_a_flag_the_server_took_keeps_the_remote_state(self):
        state = {"uidvalidity": 1, "uidnext": 2, "exists": 1, "highestmodseq": 3}
        MailFolder.objects.filter(pk=self.inbox.pk).update(remote_state=state)

        self.assertTrue(set_flag(self.message, "read"))

        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.remote_state, state)


class MarkFolderReadTests(TriageServiceTestCase):
    def test_a_message_imported_during_the_imap_calls_stays_unread(self):
        # Its UID was not in the STORE, so the server never got its \Seen:
        # marking it read here would be undone by the next sync.
        label = MailLabel.objects.get(account=self.account, name="Urgent")
        MailMessageLabel.objects.create(message=self.message, label=label)
        arrived = []

        def sync_imports_one_more(account, mailbox, uids):
            newcomer = MailMessage.objects.create(
                account=self.account, folder=self.inbox, imap_uid=2, is_read=False
            )
            MailMessageLabel.objects.create(message=newcomer, label=label)
            MailLabel.objects.filter(pk=label.pk).update(unread_count=2)
            arrived.append(newcomer)

        with patch(
            "workspace.mail.services.imap_messages.mark_uids_read",
            side_effect=sync_imports_one_more,
        ) as mark:
            updated, synced = mark_folder_read(self.inbox)

        mark.assert_called_once_with(self.account, self.inbox, [1])
        self.assertEqual((updated, synced), (1, True))
        self.message.refresh_from_db()
        arrived[0].refresh_from_db()
        self.inbox.refresh_from_db()
        label.refresh_from_db()
        self.assertTrue(self.message.is_read)
        self.assertFalse(arrived[0].is_read)
        self.assertEqual(self.inbox.unread_count, 1)
        self.assertEqual(label.unread_count, 1)

    def test_a_folder_holding_more_unread_than_a_statement_binds(self):
        limit = 50
        MailMessage.objects.bulk_create(
            MailMessage(
                account=self.account, folder=self.inbox, imap_uid=uid, is_read=False
            )
            for uid in range(2, limit * 2 + 1)
        )
        with (
            patch("workspace.mail.services.imap_messages.mark_uids_read"),
            sqlite_parameter_limit(limit),
        ):
            updated, synced = mark_folder_read(self.inbox)

        self.assertEqual((updated, synced), (limit * 2, True))
        unread = MailMessage.objects.filter(folder=self.inbox, is_read=False)
        self.assertFalse(unread.exists())

    def test_a_message_moved_out_during_the_imap_calls_stays_unread(self):
        # The STORE went to the Inbox; the copy now in the Archive never got
        # its \Seen, so marking the row read there would be undone by the
        # next sync of the Archive.
        def moved_to_archive(account, mailbox, uids):
            MailMessage.objects.filter(pk=self.message.pk).update(
                folder=self.archive, imap_uid=900
            )

        with patch(
            "workspace.mail.services.imap_messages.mark_uids_read",
            side_effect=moved_to_archive,
        ):
            updated, _synced = mark_folder_read(self.inbox)

        self.message.refresh_from_db()
        self.assertEqual(updated, 0)
        self.assertFalse(self.message.is_read)

    def test_a_message_moved_out_and_back_during_the_imap_calls_stays_unread(self):
        # Back in the Inbox under a new UID: the STORE flagged the old one, so
        # the copy that is there now never got its \Seen.
        def moved_out_and_back(account, mailbox, uids):
            MailMessage.objects.filter(pk=self.message.pk).update(imap_uid=901)

        with patch(
            "workspace.mail.services.imap_messages.mark_uids_read",
            side_effect=moved_out_and_back,
        ):
            updated, _synced = mark_folder_read(self.inbox)

        self.message.refresh_from_db()
        self.assertEqual(updated, 0)
        self.assertFalse(self.message.is_read)


class MoveToFolderTests(TriageServiceTestCase):
    def test_a_move_repoints_the_row_and_refreshes_both_folders(self):
        with patch(
            "workspace.mail.services.imap_messages.move_message", return_value=900
        ) as move:
            source = move_to_folder(self.message, self.archive)

        self.assertEqual(source, self.inbox.pk)
        self.message.refresh_from_db()
        self.inbox.refresh_from_db()
        self.archive.refresh_from_db()
        self.assertEqual(self.message.folder, self.archive)
        self.assertEqual(self.inbox.unread_count, 0)
        self.assertEqual(self.archive.message_count, 1)
        move.assert_called_once_with(self.account, self.message, self.archive)

    def test_a_refused_move_raises_and_writes_nothing(self):
        # Re-pointing the row while the server still holds the message where
        # it was gives the next sync a message it cannot find, and it
        # soft-deletes it.
        with patch(
            "workspace.mail.services.imap_messages.move_message",
            side_effect=Exception("IMAP COPY failed"),
        ):
            with self.assertRaises(Exception):
                move_to_folder(self.message, self.archive)

        self.message.refresh_from_db()
        self.assertEqual(self.message.folder, self.inbox)

    def test_refresh_can_be_deferred_to_a_batching_caller(self):
        with patch(
            "workspace.mail.services.imap_messages.move_message", return_value=900
        ):
            move_to_folder(self.message, self.archive, refresh=False)

        self.message.refresh_from_db()
        self.inbox.refresh_from_db()
        self.assertEqual(self.message.folder, self.archive)
        self.assertEqual(self.inbox.unread_count, 99, "counters left to the caller")

    def _move(self, copied_uid):
        with patch(
            "workspace.mail.services.imap_messages.move_message",
            return_value=copied_uid,
        ):
            move_to_folder(self.message, self.archive)
        self.message.refresh_from_db()

    def test_the_row_takes_the_uid_of_the_copy(self):
        self._move(900)

        self.assertEqual(self.message.folder, self.archive)
        self.assertEqual(self.message.imap_uid, 900)
        self.assertIsNone(self.message.deleted_at)

    def test_an_unknown_copy_uid_retires_the_row_where_it_was(self):
        # The source UID means another message in the target, if anything.
        self._move(None)

        self.assertEqual(self.message.folder, self.inbox)
        self.assertEqual(self.message.imap_uid, 1)
        self.assertIsNotNone(self.message.deleted_at)
        self.archive.refresh_from_db()
        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.message_count, 0)

    def test_a_retired_unread_message_leaves_its_label_counts(self):
        label = MailLabel.objects.get(account=self.account, name="Urgent")
        MailMessageLabel.objects.create(message=self.message, label=label)
        MailLabel.objects.filter(pk=label.pk).update(unread_count=1)

        with patch(
            "workspace.mail.services.imap_messages.move_message", return_value=None
        ):
            move_to_folder(self.message, self.archive, refresh=False)

        label.refresh_from_db()
        self.assertEqual(label.unread_count, 0)

    def test_a_copy_the_target_sync_already_imported_is_not_claimed_twice(self):
        MailMessage.objects.create(
            account=self.account, folder=self.archive, imap_uid=900
        )

        self._move(900)

        self.assertEqual(self.message.folder, self.inbox)
        self.assertIsNotNone(self.message.deleted_at)

    def test_a_concurrent_move_that_already_repointed_the_row_wins(self):
        # The first request expunged the source; this one's COPY of the
        # vanished UID answered OK with nothing copied, hence no copy UID.
        def first_move_lands(account, message, target):
            MailMessage.objects.filter(pk=message.pk).update(
                folder=self.archive, imap_uid=900
            )

        with patch(
            "workspace.mail.services.imap_messages.move_message",
            side_effect=first_move_lands,
        ):
            move_to_folder(self.message, self.archive)

        self.message.refresh_from_db()
        self.assertEqual(self.message.folder, self.archive)
        self.assertEqual(self.message.imap_uid, 900)
        self.assertIsNone(self.message.deleted_at)

    def test_a_copy_imported_right_after_the_check_retires_the_row(self):
        # The target's sync commits the copy between the existence check and
        # the write: the write hits the (folder, imap_uid) constraint after
        # the server move has already happened.
        real_exists = QuerySet.exists
        imported = []

        def exists_then_sync_imports(queryset):
            found = real_exists(queryset)
            if queryset.model is MailMessage and not imported:
                imported.append(
                    MailMessage.objects.create(
                        account=self.account, folder=self.archive, imap_uid=900
                    )
                )
            return found

        with patch.object(QuerySet, "exists", exists_then_sync_imports):
            self._move(900)

        self.message.refresh_from_db()
        imported[0].refresh_from_db()
        self.assertEqual(self.message.folder, self.inbox)
        self.assertIsNotNone(self.message.deleted_at)
        self.assertIsNone(imported[0].deleted_at)

    def test_the_target_sync_keeps_the_moved_row(self):
        """The row survives the target's next sync, labels and all, and the
        copy is not imported a second time."""
        from workspace.mail.services.imap_sync import sync_folder_messages

        label = MailLabel.objects.get(account=self.account, name="Urgent")
        MailMessageLabel.objects.create(message=self.message, label=label)
        with fake_imap() as server:
            server.deliver(
                "INBOX", uid=1, subject="Invoice", message_id="<inv@example.com>"
            )
            # The Archive's counter is at 900: the copy takes that UID.
            server.remove("Archive", server.deliver("Archive", uid=899))
            MailFolder.objects.filter(pk=self.archive.pk).update(
                uid_validity=server.mailbox("Archive").uidvalidity, last_sync_uid=899
            )
            self.archive.refresh_from_db()

            move_to_folder(self.message, self.archive)
            sync_folder_messages(self.account, self.archive)

        self.assertEqual(server.uids("Archive"), [900])
        self.message.refresh_from_db()
        self.assertEqual(self.message.imap_uid, 900)
        live = MailMessage.objects.filter(folder=self.archive, deleted_at__isnull=True)
        self.assertEqual(list(live), [self.message])
        self.assertEqual(MailMessage.objects.filter(folder=self.archive).count(), 1)
        self.assertTrue(
            MailMessageLabel.objects.filter(message=self.message, label=label).exists()
        )


class SetLabelTests(TriageServiceTestCase):
    def setUp(self):
        super().setUp()
        self.label = MailLabel.objects.get(account=self.account, name="Urgent")

    def test_attaching_reports_the_change_and_counts_the_unread(self):
        self.assertTrue(set_label(self.message, self.label, True))
        self.label.refresh_from_db()
        self.assertEqual(self.label.unread_count, 1)

    def test_attaching_twice_reports_no_change(self):
        set_label(self.message, self.label, True)
        self.assertFalse(set_label(self.message, self.label, True))
        self.assertEqual(
            MailMessageLabel.objects.filter(message=self.message).count(), 1
        )

    def test_detaching_reports_the_change_and_drops_the_count(self):
        set_label(self.message, self.label, True)
        self.assertTrue(set_label(self.message, self.label, False))
        self.label.refresh_from_db()
        self.assertEqual(self.label.unread_count, 0)

    def test_detaching_what_was_never_attached_reports_no_change(self):
        self.assertFalse(set_label(self.message, self.label, False))
