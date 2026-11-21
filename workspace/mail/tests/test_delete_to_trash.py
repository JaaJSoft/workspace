"""Deleting a message moves it to the account's Trash; only Trash expunges.

Regression: every delete path (the message endpoint, the batch endpoint and
the rules' delete action) sent ``UID STORE +FLAGS (\\Deleted)`` then
``EXPUNGE`` to the message's own folder, so a message deleted from the Inbox
was gone from the server for good while the UI promised a move to trash.

These tests run the real services against an in-memory server, so they
assert what the server holds afterwards.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.rules.actions import apply_action
from workspace.mail.services.rules.schema import parse_actions
from workspace.mail.tests.imap_fake import BARE, FULL, fake_imap
from workspace.notifications.models import Notification

User = get_user_model()

MESSAGE_ID = "<m42@example.com>"
INBOX_UID = 42
# A message another mail program left in Trash, flagged but not expunged.
OTHER_TRASH_UID = 899
TRASH_UID = 900


class DeleteToTrashMixin:
    capabilities = FULL

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
            imap_uid=INBOX_UID,
            message_id=MESSAGE_ID,
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

        self.server = self.enterContext(fake_imap(capabilities=self.capabilities))
        self.server.deliver("INBOX", uid=INBOX_UID, message_id=MESSAGE_ID)
        self.server.deliver("Trash", uid=OTHER_TRASH_UID, flags=[r"\Deleted"])

    def _file_under(self, folder):
        """Put the message in `folder`, on the server and locally."""
        self.server.remove("INBOX", self.msg.imap_uid)
        self.msg.imap_uid = self.server.deliver(folder.name, message_id=MESSAGE_ID)
        self.msg.folder = folder
        self.msg.save(update_fields=["folder", "imap_uid"])

    def _refuse_the_transfer(self):
        self.server.fail("move" if "MOVE" in self.capabilities else "copy")

    def assert_moved_to_trash(self):
        self.assertEqual(self.server.uids("INBOX"), [])
        self.assertEqual(self.server.uids("Trash"), [OTHER_TRASH_UID, TRASH_UID])
        self.assertIn(
            MESSAGE_ID.encode(), self.server.mailbox("Trash").messages[TRASH_UID].raw
        )
        self.assertNotIn(r"\Deleted", self.server.flags("Trash", TRASH_UID))

        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder, self.trash)
        self.assertEqual(self.msg.imap_uid, TRASH_UID)
        self.assertIsNone(self.msg.deleted_at)
        self.notif.refresh_from_db()
        self.assertIsNotNone(self.notif.read_at)

    def assert_left_in_the_inbox(self):
        self.assertEqual(self.server.uids("INBOX"), [INBOX_UID])
        self.assertEqual(self.server.flags("INBOX", INBOX_UID), set())
        self.assertEqual(self.server.uids("Trash"), [OTHER_TRASH_UID])
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder, self.inbox)
        self.assertEqual(self.msg.imap_uid, INBOX_UID)
        self.assertIsNone(self.msg.deleted_at)

    def assert_expunged(self, mailbox):
        """Gone from `mailbox` for good: no copy was kept anywhere."""
        self.assertNotIn(self.msg.imap_uid, self.server.uids(mailbox))
        self.assertEqual(self.server.named("copy", "move"), [])
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
        self._file_under(self.trash)
        resp = self._delete()
        self.assertEqual(resp.status_code, 204)
        self.assert_expunged("Trash")

    def test_delete_from_trash_expunges_that_message_alone(self):
        """With UIDPLUS the expunge names the UID: a message another mail
        program flagged ``\\Deleted`` without expunging stays (issue #1513)."""
        self._file_under(self.trash)
        self._delete()
        self.assertEqual(self.server.uids("Trash"), [OTHER_TRASH_UID])
        self.assertEqual(self.server.named("expunge"), [])

    def test_delete_from_a_folder_merged_into_trash_expunges(self):
        corbeille = MailFolder.objects.create(
            account=self.account,
            name="Corbeille",
            display_name="Corbeille",
            folder_type="trash",
            alias_of=self.trash,
        )
        self._file_under(corbeille)
        self._delete()
        self.assert_expunged("Corbeille")

    def test_account_without_trash_expunges(self):
        self.trash.delete()
        resp = self._delete()
        self.assertEqual(resp.status_code, 204)
        self.assert_expunged("INBOX")
        self.assertEqual(self.server.uids("Trash"), [OTHER_TRASH_UID])

    def test_refused_transfer_leaves_message_in_place(self):
        self._refuse_the_transfer()
        resp = self._delete()
        self.assertEqual(resp.status_code, 502)
        self.assert_left_in_the_inbox()


class MessageDeleteWithoutExtensionsTests(DeleteToTrashMixin, TestCase):
    """A server with neither MOVE nor UIDPLUS."""

    capabilities = BARE

    def _delete(self):
        return self.client.delete(f"/api/v1/mail/messages/{self.msg.uuid}")

    def test_delete_from_inbox_copies_to_trash_then_expunges_the_source(self):
        resp = self._delete()
        self.assertEqual(resp.status_code, 204)
        self.assert_moved_to_trash()
        self.assertEqual(
            [command[0] for command in self.server.named("copy", "expunge")],
            ["copy", "expunge"],
        )

    def test_refused_copy_leaves_message_in_place(self):
        """The source is flagged and expunged only once the copy exists."""
        self._refuse_the_transfer()
        resp = self._delete()
        self.assertEqual(resp.status_code, 502)
        self.assert_left_in_the_inbox()
        self.assertEqual(self.server.named("add_flags", "expunge"), [])

    def test_delete_from_trash_falls_back_to_the_plain_expunge(self):
        self._file_under(self.trash)
        self._delete()
        self.assert_expunged("Trash")
        self.assertEqual(len(self.server.named("expunge")), 1)
        self.assertEqual(self.server.named("uid_expunge"), [])


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
        self._file_under(self.trash)
        self._delete()
        self.assert_expunged("Trash")
        self.assertEqual(self.server.uids("Trash"), [OTHER_TRASH_UID])

    def test_refused_transfer_is_not_counted(self):
        self._refuse_the_transfer()
        resp = self._delete()
        self.assertEqual(resp.json()["processed"], 0)
        self.assert_left_in_the_inbox()


class RuleDeleteTests(DeleteToTrashMixin, TestCase):
    def _apply(self):
        return apply_action(parse_actions([{"type": "delete"}])[0], self.msg)

    def test_rule_delete_moves_to_trash(self):
        result = self._apply()
        self.assertTrue(result["ok"])
        self.assert_moved_to_trash()

    def test_rule_never_expunges_from_trash(self):
        self._file_under(self.trash)
        result = self._apply()
        self.assertTrue(result["ok"])
        self.assertTrue(result["noop"])
        self.assertEqual(self.server.commands, [])
        self.assertEqual(self.server.uids("Trash"), [OTHER_TRASH_UID, TRASH_UID])
        self.msg.refresh_from_db()
        self.assertIsNone(self.msg.deleted_at)
        self.notif.refresh_from_db()
        self.assertIsNotNone(self.notif.read_at)

    def test_rule_never_expunges_without_trash(self):
        self.trash.delete()
        result = self._apply()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "no_trash_folder")
        self.assertEqual(self.server.commands, [])
        self.assertEqual(self.server.uids("INBOX"), [INBOX_UID])
        self.msg.refresh_from_db()
        self.assertIsNone(self.msg.deleted_at)

    def test_refused_transfer_is_reported(self):
        self._refuse_the_transfer()
        result = self._apply()
        self.assertEqual(result["error"], "imap_failed")
        self.assert_left_in_the_inbox()
