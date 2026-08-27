import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.mail.ai_tools import (
    DeleteEmailParams,
    LabelEmailParams,
    ListMailAccountScopedParams,
    MailToolProvider,
    MarkEmailParams,
    MoveEmailParams,
)
from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailLabel,
    MailMessage,
    MailMessageLabel,
)

User = get_user_model()


class MailTriageToolsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="triager", password="pw")
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
        )
        self.archive = MailFolder.objects.create(
            account=self.account,
            name="Archive",
            display_name="Archive",
            folder_type="archive",
        )
        self.trash = MailFolder.objects.create(
            account=self.account,
            name="Trash",
            display_name="Trash",
            folder_type="trash",
        )
        self.message = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=1,
            subject="Invoice",
            from_email="alice@example.com",
        )
        self.tools = MailToolProvider()

    def tearDown(self):
        cache.clear()

    def _call(self, tool, params):
        return tool(params, user=self.user, bot=None, conversation_id=None, context={})


class ListFoldersAndLabelsTests(MailTriageToolsTestCase):
    def test_folders_are_listed_with_their_counts(self):
        MailFolder.objects.filter(pk=self.inbox.pk).update(
            message_count=12, unread_count=3
        )
        result = self._call(self.tools.list_folders, ListMailAccountScopedParams())
        folders = {entry["name"]: entry for entry in json.loads(result)}
        self.assertEqual(set(folders), {"Inbox", "Archive", "Trash"})
        self.assertEqual(folders["Inbox"]["unread"], 3)
        self.assertEqual(folders["Inbox"]["type"], "inbox")

    def test_labels_are_scoped_to_the_account(self):
        MailLabel.objects.filter(account=self.account, name="Urgent").update(
            unread_count=2
        )
        other = MailAccount.objects.create(
            owner=self.user,
            email="other@example.com",
            is_active=False,
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="other@example.com",
        )
        MailLabel.objects.create(account=other, name="Elsewhere")

        labels = json.loads(
            self._call(self.tools.list_labels, ListMailAccountScopedParams())
        )
        names = [entry["name"] for entry in labels]
        self.assertNotIn("Elsewhere", names)
        self.assertEqual(names[0], "Urgent")
        self.assertEqual(labels[0]["unread"], 2)

    def test_no_account_is_reported(self):
        self.account.delete()
        result = self._call(self.tools.list_folders, ListMailAccountScopedParams())
        self.assertIn("No active mail account", result)


class MarkEmailTests(MailTriageToolsTestCase):
    def test_starring_updates_the_row_and_the_server(self):
        with patch("workspace.mail.services.imap_messages.star_message") as star:
            result = self._call(
                self.tools.mark_email,
                MarkEmailParams(uuid=self.message.uuid, action="starred"),
            )
        self.message.refresh_from_db()
        self.assertTrue(self.message.is_starred)
        self.assertIn("is now starred", result)
        star.assert_called_once()

    def test_marking_read_refreshes_the_folder_counts(self):
        MailFolder.objects.filter(pk=self.inbox.pk).update(unread_count=99)
        with patch("workspace.mail.services.imap_messages.mark_read"):
            self._call(
                self.tools.mark_email,
                MarkEmailParams(uuid=self.message.uuid, action="read"),
            )
        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.unread_count, 0)

    def test_an_imap_failure_is_reported_but_the_local_flag_holds(self):
        with patch(
            "workspace.mail.services.imap_messages.mark_unread",
            side_effect=OSError("connection reset"),
        ):
            result = self._call(
                self.tools.mark_email,
                MarkEmailParams(uuid=self.message.uuid, action="unread"),
            )
        self.message.refresh_from_db()
        self.assertFalse(self.message.is_read)
        self.assertIn("is now unread here, but", result)

    def test_unknown_message(self):
        import uuid as uuid_mod

        result = self._call(
            self.tools.mark_email,
            MarkEmailParams(uuid=uuid_mod.uuid4(), action="read"),
        )
        self.assertEqual(result, "Email not found or access denied.")


class MoveEmailTests(MailTriageToolsTestCase):
    def test_move_by_folder_name(self):
        with patch("workspace.mail.services.imap_messages.move_message") as move:
            result = self._call(
                self.tools.move_email,
                MoveEmailParams(uuid=self.message.uuid, folder="archive"),
            )
        self.message.refresh_from_db()
        self.assertEqual(self.message.folder, self.archive)
        self.assertIn("moved to Archive", result)
        self.assertEqual(move.call_args.args[2], self.archive)

    def test_unknown_folder_lists_nothing_and_moves_nothing(self):
        with patch("workspace.mail.services.imap_messages.move_message") as move:
            result = self._call(
                self.tools.move_email,
                MoveEmailParams(uuid=self.message.uuid, folder="Nowhere"),
            )
        self.assertIn("Call list_folders", result)
        move.assert_not_called()
        self.message.refresh_from_db()
        self.assertEqual(self.message.folder, self.inbox)

    def test_a_failed_imap_move_leaves_the_row_where_it_was(self):
        # Moving the row while the server still holds the message in its old
        # folder makes the next sync soft-delete a message nobody deleted.
        with patch(
            "workspace.mail.services.imap_messages.move_message",
            side_effect=Exception("IMAP COPY failed"),
        ):
            result = self._call(
                self.tools.move_email,
                MoveEmailParams(uuid=self.message.uuid, folder="Archive"),
            )
        self.message.refresh_from_db()
        self.assertEqual(self.message.folder, self.inbox)
        self.assertIn("did not complete the move", result)

    def test_moving_to_the_current_folder_is_a_no_op(self):
        with patch("workspace.mail.services.imap_messages.move_message") as move:
            result = self._call(
                self.tools.move_email,
                MoveEmailParams(uuid=self.message.uuid, folder="Inbox"),
            )
        self.assertIn("already in Inbox", result)
        move.assert_not_called()


class DeleteEmailTests(MailTriageToolsTestCase):
    def test_delete_moves_to_the_trash_folder(self):
        with patch("workspace.mail.services.imap_messages.move_message") as move:
            result = self._call(
                self.tools.delete_email, DeleteEmailParams(uuid=self.message.uuid)
            )
        self.message.refresh_from_db()
        self.assertEqual(self.message.folder, self.trash)
        self.assertIsNone(self.message.deleted_at)
        self.assertIn("moved to Trash", result)
        self.assertEqual(move.call_args.args[2], self.trash)

    def test_without_a_trash_folder_nothing_is_expunged(self):
        self.trash.delete()
        with (
            patch("workspace.mail.services.imap_messages.move_message") as move,
            patch("workspace.mail.services.imap_messages.delete_message") as hard,
        ):
            result = self._call(
                self.tools.delete_email, DeleteEmailParams(uuid=self.message.uuid)
            )
        self.assertIn("no trash folder", result)
        move.assert_not_called()
        hard.assert_not_called()
        self.message.refresh_from_db()
        self.assertEqual(self.message.folder, self.inbox)


class LabelEmailTests(MailTriageToolsTestCase):
    def setUp(self):
        super().setUp()
        # Every account is seeded with a default set of labels.
        self.label = MailLabel.objects.get(account=self.account, name="Urgent")

    def test_add_and_remove(self):
        result = self._call(
            self.tools.label_email,
            LabelEmailParams(uuid=self.message.uuid, label="urgent", action="add"),
        )
        self.assertIn("Labelled", result)
        self.assertTrue(
            MailMessageLabel.objects.filter(
                message=self.message, label=self.label
            ).exists()
        )

        result = self._call(
            self.tools.label_email,
            LabelEmailParams(uuid=self.message.uuid, label="Urgent", action="remove"),
        )
        self.assertIn("Removed", result)
        self.assertFalse(
            MailMessageLabel.objects.filter(
                message=self.message, label=self.label
            ).exists()
        )

    def test_adding_twice_is_harmless(self):
        params = LabelEmailParams(uuid=self.message.uuid, label="Urgent")
        self._call(self.tools.label_email, params)
        result = self._call(self.tools.label_email, params)
        self.assertIn("already carried", result)
        self.assertEqual(
            MailMessageLabel.objects.filter(message=self.message).count(), 1
        )

    def test_unknown_label(self):
        result = self._call(
            self.tools.label_email,
            LabelEmailParams(uuid=self.message.uuid, label="Nope"),
        )
        self.assertIn("Call list_labels", result)

    def test_a_label_from_another_account_is_not_reachable(self):
        other = MailAccount.objects.create(
            owner=self.user,
            email="other@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="other@example.com",
        )
        MailLabel.objects.create(account=other, name="Foreign")
        result = self._call(
            self.tools.label_email,
            LabelEmailParams(uuid=self.message.uuid, label="Foreign"),
        )
        self.assertIn("No label named", result)

    def test_label_unread_count_is_refreshed(self):
        self.message.is_read = False
        self.message.save(update_fields=["is_read"])
        self._call(
            self.tools.label_email,
            LabelEmailParams(uuid=self.message.uuid, label="Urgent"),
        )
        self.label.refresh_from_db()
        self.assertEqual(self.label.unread_count, 1)
