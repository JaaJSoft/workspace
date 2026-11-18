"""Deleting a message moves it to the account's Trash; only Trash expunges.

Regression: every delete path (the message endpoint, the batch endpoint and
the rules' delete action) sent ``UID STORE +FLAGS (\\Deleted)`` then
``EXPUNGE`` to the message's own folder, so a message deleted from the Inbox
was gone from the server for good while the UI promised a move to trash.

These tests mock the IMAP connection, not the service functions, so they
assert the commands the server actually receives.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.rules.actions import apply_action
from workspace.mail.services.rules.schema import parse_actions
from workspace.notifications.models import Notification

User = get_user_model()

TRASH_UID = 900


def _imap_commands(conn):
    """The commands sent on `conn`, in order: ("COPY", ...), ("EXPUNGE",)."""
    commands = []
    for name, args, _kwargs in conn.mock_calls:
        if name == "uid":
            commands.append(tuple(str(a) for a in args))
        elif name == "expunge":
            commands.append(("EXPUNGE",))
    return commands


class DeleteToTrashMixin:
    def setUp(self):
        self.user = User.objects.create_user(username="trashuser", password="pass")
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
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=42,
            message_id="<m42@example.com>",
        )
        self.notif = Notification.objects.create(
            recipient=self.user,
            origin="mail",
            icon="",
            title="Hi",
            mail_message=self.msg,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        patcher = patch("workspace.mail.services.imap_messages.connect_imap")
        self.conn = patcher.start().return_value
        self.addCleanup(patcher.stop)
        self.conn.select.return_value = ("OK", [b"1"])
        self.conn.status.return_value = ("OK", [b'"Trash" (UIDNEXT 900)'])
        self.conn.response.return_value = ("COPYUID", [f"1 42 {TRASH_UID}".encode()])
        self.conn.uid.return_value = ("OK", [b""])

    def assert_moved_to_trash(self):
        commands = _imap_commands(self.conn)
        self.assertEqual(commands[0], ("COPY", "42", '"Trash"'))
        self.assertIn(("STORE", "42", "+FLAGS", "(\\Deleted)"), commands[1:])
        # The source mailbox is the one the STORE and EXPUNGE ran in.
        self.assertEqual(self.conn.select.call_args_list[0].args[0], '"INBOX"')

        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder, self.trash)
        self.assertEqual(self.msg.imap_uid, TRASH_UID)
        self.assertIsNone(self.msg.deleted_at)
        self.notif.refresh_from_db()
        self.assertIsNotNone(self.notif.read_at)

    def assert_expunged(self):
        commands = _imap_commands(self.conn)
        self.assertEqual(
            commands, [("STORE", "42", "+FLAGS", "(\\Deleted)"), ("EXPUNGE",)]
        )
        self.msg.refresh_from_db()
        self.assertIsNotNone(self.msg.deleted_at)


class MessageDeleteTests(DeleteToTrashMixin, TestCase):
    def _delete(self):
        return self.client.delete(f"/api/v1/mail/messages/{self.msg.uuid}")

    def test_delete_from_inbox_moves_to_trash(self):
        resp = self._delete()
        self.assertEqual(resp.status_code, 204)
        self.assert_moved_to_trash()

    def test_counts_follow_the_message_into_trash(self):
        self._delete()
        self.inbox.refresh_from_db()
        self.trash.refresh_from_db()
        self.assertEqual((self.inbox.message_count, self.inbox.unread_count), (0, 0))
        self.assertEqual((self.trash.message_count, self.trash.unread_count), (1, 1))

    def test_delete_from_trash_expunges(self):
        self.msg.folder = self.trash
        self.msg.save(update_fields=["folder"])
        resp = self._delete()
        self.assertEqual(resp.status_code, 204)
        self.assert_expunged()

    def test_delete_from_a_folder_merged_into_trash_expunges(self):
        corbeille = MailFolder.objects.create(
            account=self.account,
            name="Corbeille",
            display_name="Corbeille",
            folder_type="trash",
            alias_of=self.trash,
        )
        self.msg.folder = corbeille
        self.msg.save(update_fields=["folder"])
        self._delete()
        self.assert_expunged()

    def test_account_without_trash_expunges(self):
        self.trash.delete()
        resp = self._delete()
        self.assertEqual(resp.status_code, 204)
        self.assert_expunged()

    def test_refused_copy_leaves_message_in_place(self):
        self.conn.uid.side_effect = lambda command, *args: (
            ("NO", [b"Over quota"]) if command == "COPY" else ("OK", [b""])
        )
        resp = self._delete()
        self.assertEqual(resp.status_code, 502)
        commands = _imap_commands(self.conn)
        self.assertEqual([c[0] for c in commands], ["COPY"])
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder, self.inbox)
        self.assertIsNone(self.msg.deleted_at)


class BatchDeleteTests(DeleteToTrashMixin, TestCase):
    def _delete(self):
        return self.client.post(
            "/api/v1/mail/messages/batch-action",
            {"message_ids": [str(self.msg.uuid)], "action": "delete"},
            format="json",
        )

    def test_batch_delete_from_inbox_moves_to_trash(self):
        resp = self._delete()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["processed"], 1)
        self.assert_moved_to_trash()
        self.trash.refresh_from_db()
        self.assertEqual(self.trash.message_count, 1)

    def test_batch_delete_from_trash_expunges(self):
        self.msg.folder = self.trash
        self.msg.save(update_fields=["folder"])
        self._delete()
        self.assert_expunged()

    def test_refused_copy_is_not_counted(self):
        self.conn.uid.side_effect = lambda command, *args: (
            ("NO", [b"Over quota"]) if command == "COPY" else ("OK", [b""])
        )
        resp = self._delete()
        self.assertEqual(resp.json()["processed"], 0)
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder, self.inbox)
        self.assertIsNone(self.msg.deleted_at)


class RuleDeleteTests(DeleteToTrashMixin, TestCase):
    def _apply(self):
        return apply_action(parse_actions([{"type": "delete"}])[0], self.msg)

    def test_rule_delete_moves_to_trash(self):
        result = self._apply()
        self.assertTrue(result["ok"])
        self.assert_moved_to_trash()

    def test_rule_never_expunges_from_trash(self):
        self.msg.folder = self.trash
        self.msg.save(update_fields=["folder"])
        result = self._apply()
        self.assertTrue(result["ok"])
        self.assertTrue(result["noop"])
        self.assertEqual(_imap_commands(self.conn), [])
        self.msg.refresh_from_db()
        self.assertIsNone(self.msg.deleted_at)
        self.notif.refresh_from_db()
        self.assertIsNotNone(self.notif.read_at)

    def test_rule_never_expunges_without_trash(self):
        self.trash.delete()
        result = self._apply()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_trash_folder")
        self.assertEqual(_imap_commands(self.conn), [])
        self.msg.refresh_from_db()
        self.assertIsNone(self.msg.deleted_at)

    def test_refused_copy_is_reported(self):
        self.conn.uid.side_effect = lambda command, *args: (
            ("NO", [b"Over quota"]) if command == "COPY" else ("OK", [b""])
        )
        result = self._apply()
        self.assertEqual(result["error"], "imap_failed")
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder, self.inbox)
