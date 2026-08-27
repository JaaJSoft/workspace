"""The shared per-message triage service.

The rules here used to live inside the mail views; the AI tools now go
through the same functions, so each rule is pinned once, at the level both
callers share.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailLabel,
    MailMessage,
    MailMessageLabel,
)
from workspace.mail.services.triage import (
    flag_operations,
    move_to_folder,
    set_flag,
    set_label,
)

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


class MoveToFolderTests(TriageServiceTestCase):
    def test_a_move_repoints_the_row_and_refreshes_both_folders(self):
        with patch("workspace.mail.services.imap_messages.move_message") as move:
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
        with patch("workspace.mail.services.imap_messages.move_message"):
            move_to_folder(self.message, self.archive, refresh=False)

        self.message.refresh_from_db()
        self.inbox.refresh_from_db()
        self.assertEqual(self.message.folder, self.archive)
        self.assertEqual(self.inbox.unread_count, 99, "counters left to the caller")


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
