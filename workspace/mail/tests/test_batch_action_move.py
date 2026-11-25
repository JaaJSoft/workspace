"""Regression tests for the batch move action.

Pin down that an IMAP move failure leaves the local row pointing at its
original folder, otherwise DB and IMAP go out of sync: the next reconcile
would soft-delete the message that's still actually present on the server.
"""

import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.tests.imap_fake import BARE, FULL, fake_imap

User = get_user_model()


class BatchMoveMixin:
    def setUp(self):
        self.user = User.objects.create_user(username="moveuser", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
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
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.inbox,
            imap_uid=42,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)


class BatchMoveIMAPFailureTests(BatchMoveMixin, TestCase):
    @patch("workspace.mail.services.imap_messages.move_message", return_value=900)
    def test_imap_move_failure_leaves_folder_unchanged(self, mock_move):
        """If move_message raises, the message must stay in its source folder
        on the DB side: updating msg.folder optimistically and never rolling
        back creates a split-brain that the next sync mishandles."""
        mock_move.side_effect = RuntimeError("IMAP MOVE timeout")

        resp = self.client.post(
            "/api/v1/mail/messages/batch-action",
            {
                "message_ids": [str(self.msg.uuid)],
                "action": "move",
                "target_folder_id": str(self.archive.uuid),
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        # processed should not count this message - the move did not actually happen.
        self.assertEqual(resp.data.get("processed", 0), 0)

        self.msg.refresh_from_db()
        self.assertEqual(
            self.msg.folder_id,
            self.inbox.pk,
            "Message must remain in source folder when IMAP move fails",
        )

    @patch("workspace.mail.services.imap_messages.move_message", return_value=900)
    def test_imap_move_success_updates_folder(self, mock_move):
        """Sanity check: when move_message succeeds, the folder is updated."""
        mock_move.return_value = 900

        resp = self.client.post(
            "/api/v1/mail/messages/batch-action",
            {
                "message_ids": [str(self.msg.uuid)],
                "action": "move",
                "target_folder_id": str(self.archive.uuid),
            },
            format="json",
        )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("processed", 0), 1)
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder_id, self.archive.pk)


class BatchMoveOnTheServerTests(BatchMoveMixin, TestCase):
    """The batch move, down to the mailboxes the messages end up in."""

    # A message another mail program flagged in the Inbox without expunging.
    OTHER_DELETED_UID = 7

    def _server(self, capabilities=FULL):
        server = self.enterContext(fake_imap(capabilities=capabilities))
        server.deliver("INBOX", uid=self.OTHER_DELETED_UID, flags=[r"\Deleted"])
        server.deliver("INBOX", uid=42)
        server.deliver("INBOX", uid=43)
        server.deliver("Archive", uid=899)
        return server

    def _move_both(self):
        self.second = MailMessage.objects.create(
            account=self.account, folder=self.inbox, imap_uid=43
        )
        return self.client.post(
            "/api/v1/mail/messages/batch-action",
            {
                "message_ids": [str(self.msg.uuid), str(self.second.uuid)],
                "action": "move",
                "target_folder_id": str(self.archive.uuid),
            },
            format="json",
        )

    def assert_both_moved(self, server):
        self.assertEqual(server.uids("INBOX"), [self.OTHER_DELETED_UID])
        self.assertEqual(server.uids("Archive"), [899, 900, 901])
        moved = MailMessage.objects.filter(folder=self.archive, deleted_at__isnull=True)
        self.assertEqual(sorted(moved.values_list("imap_uid", flat=True)), [900, 901])
        self.assertFalse(MailMessage.objects.filter(folder=self.inbox).exists())

    def test_moves_with_the_move_command_over_one_connection(self):
        server = self._server()

        resp = self._move_both()

        self.assertEqual(resp.data["processed"], 2)
        self.assert_both_moved(server)
        self.assertEqual(len(server.named("move")), 2)
        self.assertEqual(server.named("copy", "expunge", "uid_expunge"), [])
        (connection,) = server.connections
        self.assertTrue(connection.closed)

    def test_without_move_the_expunge_takes_the_moved_messages_alone(self):
        """Issue #1513: a plain EXPUNGE also removed every other message of
        the mailbox flagged ``\\Deleted``."""
        server = self._server(capabilities=BARE | {"UIDPLUS"})

        resp = self._move_both()

        self.assertEqual(resp.data["processed"], 2)
        self.assert_both_moved(server)
        self.assertEqual(
            server.named("uid_expunge"), [("uid_expunge", [42]), ("uid_expunge", [43])]
        )
        self.assertEqual(server.named("expunge"), [])

    def test_a_refused_move_leaves_the_messages_where_they_are(self):
        server = self._server()
        server.fail("move")

        resp = self._move_both()

        self.assertEqual(resp.data["processed"], 0)
        self.assertEqual(server.uids("INBOX"), [self.OTHER_DELETED_UID, 42, 43])
        self.assertEqual(server.uids("Archive"), [899])
        self.assertEqual(MailMessage.objects.filter(folder=self.inbox).count(), 2)


class BatchMoveMergeTests(APITestCase):
    """POST /api/v1/mail/messages/batch-action with a merged target."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="batchmerge", email="batchmerge@test.com", password="pass123"
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
        self.client.force_authenticate(self.user)

    def _message(self, folder, uid):
        return MailMessage.objects.create(
            account=self.account, folder=folder, imap_uid=uid, subject="m"
        )

    @patch("workspace.mail.services.imap_messages.move_message", return_value=900)
    def test_alias_target_files_into_the_canonical(self, move):
        msg = self._message(self.inbox, 1)

        resp = self.client.post(
            "/api/v1/mail/messages/batch-action",
            {
                "message_ids": [str(msg.uuid)],
                "action": "move",
                "target_folder_id": str(self.corbeille.uuid),
            },
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        msg.refresh_from_db()
        self.assertEqual(msg.folder_id, self.trash.pk)

    @patch("workspace.mail.services.imap_messages.move_message", return_value=900)
    def test_move_within_the_group_does_nothing(self, move):
        """The message is already in the logical folder; no IMAP round trip."""
        msg = self._message(self.corbeille, 2)

        self.client.post(
            "/api/v1/mail/messages/batch-action",
            {
                "message_ids": [str(msg.uuid)],
                "action": "move",
                "target_folder_id": str(self.trash.uuid),
            },
            format="json",
        )

        move.assert_not_called()
        msg.refresh_from_db()
        self.assertEqual(msg.folder_id, self.corbeille.pk)


class BatchMoveForeignTargetTests(BatchMoveMixin, TestCase):
    """Another user's folder answers exactly like a folder that does not exist."""

    def _move_to(self, target_uuid):
        return self.client.post(
            "/api/v1/mail/messages/batch-action",
            {
                "message_ids": [str(self.msg.uuid)],
                "action": "move",
                "target_folder_id": str(target_uuid),
            },
            format="json",
        )

    def test_foreign_target_answers_like_an_unknown_one(self):
        other = User.objects.create_user(username="moveother", password="pass")
        other_account = MailAccount.objects.create(
            owner=other,
            email="other@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="other@example.com",
        )
        foreign = MailFolder.objects.create(
            account=other_account, name="Archive", folder_type="archive"
        )

        hidden = self._move_to(foreign.uuid)
        unknown = self._move_to(uuid.uuid4())

        self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(
            (hidden.status_code, hidden.content),
            (unknown.status_code, unknown.content),
        )
        self.msg.refresh_from_db()
        self.assertEqual(self.msg.folder_id, self.inbox.pk)
