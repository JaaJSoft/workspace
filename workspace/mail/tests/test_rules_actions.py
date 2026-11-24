from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import (
    MailAccount,
    MailFolder,
    MailMessage,
    MailMessageLabel,
)
from workspace.mail.services.counts import refresh_folder_counts, refresh_label_counts
from workspace.mail.services.rules.actions import apply_action
from workspace.mail.services.rules.schema import parse_actions

User = get_user_model()


class _BaseActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="au", password="p")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="a@x.com",
            imap_host="x",
            smtp_host="x",
            username="a@x.com",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
        )
        # Use one of the auto-seeded default labels for this account.
        self.label = self.account.labels.first()


class LabelActionTests(_BaseActionTests):
    def test_add_label(self):
        action = parse_actions(
            [{"type": "add_label", "label_id": str(self.label.uuid)}]
        )[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result["ok"])
        self.assertEqual(result["type"], "add_label")
        self.assertTrue(
            MailMessageLabel.objects.filter(
                message=self.msg, label=self.label
            ).exists(),
        )

    def test_add_label_twice_is_idempotent(self):
        action = parse_actions(
            [{"type": "add_label", "label_id": str(self.label.uuid)}]
        )[0]
        apply_action(action, self.msg)
        # Second call must not raise and the link must still exist exactly once.
        result = apply_action(action, self.msg)
        self.assertTrue(result["ok"])
        self.assertEqual(
            MailMessageLabel.objects.filter(message=self.msg, label=self.label).count(),
            1,
        )

    def test_add_label_unknown_returns_not_ok(self):
        import uuid

        action = parse_actions([{"type": "add_label", "label_id": str(uuid.uuid4())}])[
            0
        ]
        result = apply_action(action, self.msg)
        self.assertFalse(result["ok"])
        self.assertIn("label_not_found", result["error"])

    def test_remove_label(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        action = parse_actions(
            [{"type": "remove_label", "label_id": str(self.label.uuid)}]
        )[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result["ok"])
        self.assertFalse(
            MailMessageLabel.objects.filter(
                message=self.msg, label=self.label
            ).exists(),
        )


class FlagActionTests(_BaseActionTests):
    @patch("workspace.mail.services.imap_messages.mark_read")
    def test_mark_read(self, mock_imap):
        action = parse_actions([{"type": "mark_read"}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result["ok"])
        self.msg.refresh_from_db()
        self.assertTrue(self.msg.is_read)
        mock_imap.assert_called_once_with(self.account, self.msg)

    @patch("workspace.mail.services.imap_messages.mark_unread")
    def test_mark_unread(self, mock_imap):
        self.msg.is_read = True
        self.msg.save(update_fields=["is_read"])
        action = parse_actions([{"type": "mark_unread"}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result["ok"])
        self.msg.refresh_from_db()
        self.assertFalse(self.msg.is_read)
        mock_imap.assert_called_once()

    @patch("workspace.mail.services.imap_messages.star_message")
    def test_star(self, mock_imap):
        action = parse_actions([{"type": "star"}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result["ok"])
        self.msg.refresh_from_db()
        self.assertTrue(self.msg.is_starred)
        mock_imap.assert_called_once_with(self.account, self.msg)

    @patch("workspace.mail.services.imap_messages.unstar_message")
    def test_unstar(self, mock_imap):
        self.msg.is_starred = True
        self.msg.save(update_fields=["is_starred"])
        action = parse_actions([{"type": "unstar"}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result["ok"])
        self.msg.refresh_from_db()
        self.assertFalse(self.msg.is_starred)
        mock_imap.assert_called_once_with(self.account, self.msg)

    @patch(
        "workspace.mail.services.imap_messages.mark_read",
        side_effect=Exception("IMAP down"),
    )
    def test_mark_read_imap_failure_still_writes_db(self, _mock):
        """IMAP failure must not abort the DB update (existing pattern in
        views/messages.py MailMessageDetailView)."""
        action = parse_actions([{"type": "mark_read"}])[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result["ok"])  # DB write succeeded
        self.assertTrue(result.get("imap_warning"))
        self.msg.refresh_from_db()
        self.assertTrue(self.msg.is_read)

    @patch(
        "workspace.mail.services.imap_messages.mark_read",
        side_effect=Exception("IMAP down"),
    )
    def test_a_flag_the_server_did_not_take_makes_the_sync_read_the_folder(self, _mock):
        """The row says read and the mailbox does not, without the mailbox
        having changed: taken for untouched, the folder would be skipped."""
        folder = self.msg.folder
        type(folder).objects.filter(pk=folder.pk).update(
            remote_state={"highestmodseq": 7}
        )

        apply_action(parse_actions([{"type": "mark_read"}])[0], self.msg)

        folder.refresh_from_db()
        self.assertIsNone(folder.remote_state)
        self.assertEqual(folder.remote_state_version, 1)


class CounterRefreshTests(_BaseActionTests):
    """A rule changes what the badges count; they follow at once, as they do
    when the same change comes from the mail UI."""

    def setUp(self):
        super().setUp()
        refresh_folder_counts(self.folder)
        refresh_label_counts(self.label)

    def _apply(self, **action):
        return apply_action(parse_actions([action])[0], self.msg)

    @patch("workspace.mail.services.imap_messages.mark_read")
    def test_mark_read_lowers_the_folder_unread_count(self, _imap):
        self.assertEqual(self.folder.unread_count, 1)

        self._apply(type="mark_read")

        self.folder.refresh_from_db()
        self.assertEqual(self.folder.unread_count, 0)

    @patch("workspace.mail.services.imap_messages.mark_read")
    def test_mark_read_lowers_the_label_unread_count(self, _imap):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        refresh_label_counts(self.label)

        self._apply(type="mark_read")

        self.label.refresh_from_db()
        self.assertEqual(self.label.unread_count, 0)

    def test_add_label_raises_the_label_unread_count(self):
        self._apply(type="add_label", label_id=str(self.label.uuid))

        self.label.refresh_from_db()
        self.assertEqual(self.label.unread_count, 1)

    def test_remove_label_lowers_the_label_unread_count(self):
        MailMessageLabel.objects.create(message=self.msg, label=self.label)
        refresh_label_counts(self.label)

        self._apply(type="remove_label", label_id=str(self.label.uuid))

        self.label.refresh_from_db()
        self.assertEqual(self.label.unread_count, 0)


class MoveDeleteActionTests(_BaseActionTests):
    def setUp(self):
        super().setUp()
        self.archive = MailFolder.objects.create(
            account=self.account,
            name="Archive",
            display_name="Archive",
            folder_type="archive",
        )

    @patch("workspace.mail.services.imap_messages.move_message", return_value=900)
    def test_move_to_folder_success(self, mock_imap):
        action = parse_actions(
            [
                {"type": "move_to_folder", "folder_id": str(self.archive.uuid)},
            ]
        )[0]
        result = apply_action(action, self.msg)
        self.assertTrue(result["ok"])
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder_id, self.archive.uuid)
        mock_imap.assert_called_once()

    @patch("workspace.mail.services.imap_messages.move_message", return_value=900)
    def test_move_to_folder_takes_the_uid_of_the_copy(self, _move):
        """Same move as the UI's: the target's sync must recognise the row."""
        action = parse_actions(
            [{"type": "move_to_folder", "folder_id": str(self.archive.uuid)}]
        )[0]
        apply_action(action, self.msg)
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.imap_uid, 900)

    @patch(
        "workspace.mail.services.imap_messages.move_message",
        side_effect=Exception("IMAP down"),
    )
    def test_move_to_folder_imap_failure_keeps_local_folder(self, _mock):
        """Same invariant as MailBatchActionView.move: don't update local
        state when IMAP failed, or the next reconciliation will soft-delete
        the message."""
        orig_folder_id = self.msg.folder_id
        action = parse_actions(
            [
                {"type": "move_to_folder", "folder_id": str(self.archive.uuid)},
            ]
        )[0]
        result = apply_action(action, self.msg)
        self.assertFalse(result["ok"])
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder_id, orig_folder_id)

    @patch("workspace.mail.services.imap_messages.move_message", return_value=900)
    def test_move_to_other_account_folder_rejected(self, mock_imap):
        from django.contrib.auth import get_user_model

        UserModel = get_user_model()
        other = UserModel.objects.create_user(username="oth", password="p")
        other_account = MailAccount.objects.create(
            owner=other,
            email="o@x.com",
            imap_host="x",
            smtp_host="x",
            username="o@x.com",
        )
        bad_folder = MailFolder.objects.create(
            account=other_account,
            name="X",
            display_name="X",
            folder_type="other",
        )
        action = parse_actions(
            [
                {"type": "move_to_folder", "folder_id": str(bad_folder.uuid)},
            ]
        )[0]
        result = apply_action(action, self.msg)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "folder_not_in_account")
        mock_imap.assert_not_called()


class MoveToFolderMergeTests(TestCase):
    """A rule targeting a folder that later became an alias keeps working."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="rulemerge", email="rulemerge@test.com", password="pass123"
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.inbox = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        self.trash = MailFolder.objects.create(
            account=self.account,
            name="Trash",
            display_name="Trash",
            folder_type="trash",
        )
        self.corbeille = MailFolder.objects.create(
            account=self.account,
            name="Corbeille",
            display_name="Corbeille",
            folder_type="trash",
            alias_of=self.trash,
        )

    def _message(self, folder, uid):
        return MailMessage.objects.create(
            account=self.account, folder=folder, imap_uid=uid, subject="m"
        )

    @patch("workspace.mail.services.imap_messages.move_message", return_value=900)
    def test_alias_target_resolves_to_the_canonical(self, move):
        from workspace.mail.services.rules.actions import _move_to_folder
        from workspace.mail.services.rules.schema import MoveToFolderAction

        msg = self._message(self.inbox, 1)
        action = MoveToFolderAction(
            type="move_to_folder", folder_id=self.corbeille.uuid
        )

        result = _move_to_folder(action, msg)

        msg.refresh_from_db()
        self.assertEqual(msg.folder_id, self.trash.pk)
        self.assertEqual(result["folder_id"], str(self.trash.uuid))

    @patch("workspace.mail.services.imap_messages.move_message", return_value=900)
    def test_move_within_the_group_is_a_noop(self, move):
        from workspace.mail.services.rules.actions import _move_to_folder
        from workspace.mail.services.rules.schema import MoveToFolderAction

        msg = self._message(self.corbeille, 2)
        action = MoveToFolderAction(type="move_to_folder", folder_id=self.trash.uuid)

        result = _move_to_folder(action, msg)

        move.assert_not_called()
        self.assertTrue(result["noop"])
