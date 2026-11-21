"""`imap_session` and `shared_connections`: who opens a connection, who gets
the one already open, and when it is thrown away."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError

from workspace.mail.models import MailAccount
from workspace.mail.services.imap_connection import imap_session, shared_connections
from workspace.mail.tests.imap_fake import fake_imap

User = get_user_model()


class TimeLimit(Exception):
    """Stands for what a task queue raises wherever the task happens to be
    when its time is up."""


class ImapSessionTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="session", password="pw")
        self.account = self._account(user, "one@example.com")
        self.other_account = self._account(user, "two@example.com")
        self.server = self.enterContext(fake_imap())

    def _account(self, user, email):
        return MailAccount.objects.create(
            owner=user,
            email=email,
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username=email,
        )

    def test_a_block_opens_a_connection_and_logs_out(self):
        with imap_session(self.account) as client:
            self.assertFalse(client.closed)

        self.assertEqual(len(self.server.connections), 1)
        self.assertTrue(client.closed)
        self.assertEqual(self.server.named("logout"), [("logout",)])

    def test_nested_blocks_share_the_connection_of_the_outermost(self):
        with imap_session(self.account) as outer:
            with imap_session(self.account) as inner:
                self.assertIs(inner, outer)
            self.assertFalse(outer.closed, "the inner block does not log out")

        self.assertEqual(len(self.server.connections), 1)
        self.assertTrue(outer.closed)

    def test_shared_connections_opens_one_per_account_when_first_needed(self):
        with shared_connections():
            self.assertEqual(self.server.connections, [])
            with imap_session(self.account) as first:
                pass
            with imap_session(self.account) as again:
                self.assertIs(again, first)
            with imap_session(self.other_account) as other:
                self.assertIsNot(other, first)
            self.assertFalse(first.closed)

        self.assertEqual(len(self.server.connections), 2)
        self.assertTrue(first.closed)
        self.assertTrue(other.closed)

    def test_a_refused_command_keeps_the_connection(self):
        with shared_connections():
            with self.assertRaises(IMAPClientError):
                with imap_session(self.account) as first:
                    first.select_folder("No such folder")
            with imap_session(self.account) as second:
                self.assertIs(second, first)

    def _block_cut_short_by(self, error):
        """Run a block that `error` interrupts, and return its connection."""
        client = None
        try:
            with imap_session(self.account) as client:
                raise error
        except type(error):
            pass
        return client

    def assert_dropped_by(self, error):
        with shared_connections():
            first = self._block_cut_short_by(error)
            self.assertTrue(first.closed)
            with imap_session(self.account) as second:
                self.assertIsNot(second, first)

    def test_a_lost_connection_is_dropped(self):
        for error in (
            IMAPClientAbortError("socket error: EOF"),
            TimeoutError("timed out"),
        ):
            with self.subTest(error=error):
                self.assert_dropped_by(error)

    def test_an_error_that_may_have_cut_a_command_short_drops_the_connection(self):
        """A time limit falls anywhere, in the middle of a FETCH as well:
        the answer still on its way would be read as the next command's."""
        for error in (TimeLimit(), KeyboardInterrupt()):
            with self.subTest(error=error):
                self.assert_dropped_by(error)

    def test_a_connection_that_could_not_be_opened_is_tried_again(self):
        with shared_connections():
            with (
                patch(
                    "workspace.mail.services.imap_connection.connect_imap",
                    side_effect=OSError("connection refused"),
                ),
                self.assertRaises(OSError),
            ):
                with imap_session(self.account):
                    self.fail("no connection to hand out")

            with imap_session(self.account) as client:
                self.assertFalse(client.closed)

        self.assertEqual(len(self.server.connections), 1)
