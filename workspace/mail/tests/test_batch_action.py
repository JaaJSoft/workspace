from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.common.tests.parameter_limit import sqlite_parameter_limit
from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.tests.imap_fake import fake_imap

User = get_user_model()

PARAMETER_LIMIT = 50


class BatchActionLargeSelectionTests(TestCase):
    """A selection of more messages than SQLite binds in one statement."""

    def setUp(self):
        self.user = User.objects.create_user(username="batchuser", password="pass")
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
        self.messages = MailMessage.objects.bulk_create(
            MailMessage(
                account=self.account, folder=self.inbox, imap_uid=uid, is_read=False
            )
            for uid in range(1, PARAMETER_LIMIT * 2 + 1)
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    @patch("workspace.mail.services.imap_messages.mark_read")
    def test_marks_the_whole_selection_read(self, _mark_read):
        with sqlite_parameter_limit(PARAMETER_LIMIT):
            resp = self.client.post(
                "/api/v1/mail/messages/batch-action",
                {
                    "message_ids": [str(message.uuid) for message in self.messages],
                    "action": "mark_read",
                },
                format="json",
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["processed"], len(self.messages))
        unread = MailMessage.objects.filter(folder=self.inbox, is_read=False)
        self.assertFalse(unread.exists())


class TwoMessageBatchMixin:
    def setUp(self):
        self.user = User.objects.create_user(username="syncuser", password="pass")
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
        self.messages = [
            MailMessage.objects.create(
                account=self.account, folder=self.inbox, imap_uid=uid
            )
            for uid in (1, 2)
        ]
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _post(self, action):
        return self.client.post(
            "/api/v1/mail/messages/batch-action",
            {"message_ids": [str(m.uuid) for m in self.messages], "action": action},
            format="json",
        )


class BatchActionSyncOutcomeTests(TwoMessageBatchMixin, TestCase):
    """A flag the server refused is still applied locally, and the answer says so."""

    @patch("workspace.mail.services.imap_messages.star_message")
    def test_reports_synced_when_the_server_took_every_flag(self, _star):
        resp = self._post("star")

        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.data["synced"], True)

    @patch("workspace.mail.services.imap_messages.mark_read")
    def test_one_refusal_reports_unsynced_and_keeps_the_local_flag(self, mark_read):
        mark_read.side_effect = [None, OSError("connection reset")]

        resp = self._post("mark_read")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["processed"], 2)
        self.assertIs(resp.data["synced"], False)
        self.assertFalse(
            MailMessage.objects.filter(folder=self.inbox, is_read=False).exists()
        )


class BatchActionOnTheServerTests(TwoMessageBatchMixin, TestCase):
    """The batch endpoint, down to the mailbox the flags are written to."""

    def setUp(self):
        super().setUp()
        self.server = self.enterContext(fake_imap())
        for message in self.messages:
            self.server.deliver("INBOX", uid=message.imap_uid)

    def _server_flags(self):
        return [self.server.flags("INBOX", m.imap_uid) for m in self.messages]

    def test_the_flags_reach_the_mailbox_over_one_connection(self):
        resp = self._post("mark_read")

        self.assertIs(resp.data["synced"], True)
        self.assertEqual(self._server_flags(), [{r"\Seen"}, {r"\Seen"}])
        (connection,) = self.server.connections
        self.assertTrue(connection.closed)

    def test_a_store_answered_no_reports_unsynced(self):
        """Issue #1740: the answer to the STORE was not read, so a flag the
        server refused was reported as synced."""
        self.server.fail("add_flags")

        resp = self._post("star")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["processed"], 2)
        self.assertIs(resp.data["synced"], False)
        self.assertEqual(self._server_flags(), [set(), set()])
        self.assertFalse(
            MailMessage.objects.filter(folder=self.inbox, is_starred=False).exists()
        )

    def test_one_store_answered_no_reports_unsynced(self):
        self.server.fail("add_flags", times=1)

        resp = self._post("mark_read")

        self.assertIs(resp.data["synced"], False)
        self.assertEqual(sorted(map(len, self._server_flags())), [0, 1])
        self.assertFalse(
            MailMessage.objects.filter(folder=self.inbox, is_read=False).exists()
        )
