"""Regression tests for workspace.mail.services.imap_messages.move_message.

Critical invariant: if the server refuses to put the message in the target,
the source must NOT be marked \\Deleted nor expunged - otherwise the message
is lost with no copy at the target.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError
from imapclient.imapclient import literal

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_connection import shared_connections
from workspace.mail.services.imap_messages import mark_read, move_message
from workspace.mail.tests.imap_fake import BARE, FULL, FakeImapClient, fake_imap

User = get_user_model()

MESSAGE_ID = "<abc@example.com>"
SOURCE_UID = 42
# A message another mail program flagged in the Inbox without expunging.
OTHER_DELETED_UID = 41
# The UID the Archive gives the next message it takes.
COPY_UID = 100

TRANSFER_COMMANDS = ("move", "copy", "add_flags", "expunge", "uid_expunge")


class MoveMessageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="moveu", password="pass")
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
            imap_uid=SOURCE_UID,
        )

    def _server(self, capabilities=FULL, message_id=""):
        server = self.enterContext(fake_imap(capabilities=capabilities))
        server.deliver("INBOX", uid=OTHER_DELETED_UID, flags=[r"\Deleted"])
        server.deliver("INBOX", uid=SOURCE_UID, message_id=message_id)
        server.deliver("Archive", uid=COPY_UID - 1)
        return server

    def _transfer(self, server):
        return [command[0] for command in server.named(*TRANSFER_COMMANDS)]

    def assert_moved(self, server):
        self.assertNotIn(SOURCE_UID, server.uids("INBOX"))
        self.assertEqual(server.uids("Archive"), [COPY_UID - 1, COPY_UID])
        self.assertNotIn(r"\Deleted", server.flags("Archive", COPY_UID))

    def assert_untouched(self, server):
        self.assertEqual(server.uids("INBOX"), [OTHER_DELETED_UID, SOURCE_UID])
        self.assertEqual(server.flags("INBOX", SOURCE_UID), set())
        self.assertEqual(server.uids("Archive"), [COPY_UID - 1])

    def test_copy_failure_does_not_delete_source(self):
        """If COPY is answered NO, the source must NOT be STOREd \\Deleted
        nor expunged - otherwise the message is permanently lost."""
        server = self._server(capabilities=BARE)
        server.fail("copy")

        with self.assertRaises(IMAPClientError):
            move_message(self.account, self.msg, self.archive)

        self.assertEqual(self._transfer(server), ["copy"])
        self.assert_untouched(server)

    def test_move_failure_leaves_the_source(self):
        server = self._server()
        server.fail("move")

        with self.assertRaises(IMAPClientError):
            move_message(self.account, self.msg, self.archive)

        self.assertEqual(self._transfer(server), ["move"])
        self.assert_untouched(server)

    def test_copy_success_runs_store_and_expunge(self):
        """Without MOVE: once COPY succeeds, STORE \\Deleted and EXPUNGE
        follow in that order to complete the move."""
        server = self._server(capabilities=BARE)

        move_message(self.account, self.msg, self.archive)

        self.assertEqual(self._transfer(server), ["copy", "add_flags", "expunge"])
        self.assertEqual(
            server.named("add_flags"), [("add_flags", [SOURCE_UID], [r"\Deleted"])]
        )
        self.assert_moved(server)

    def test_move_is_used_when_the_server_offers_it(self):
        """One MOVE and nothing else: no COPY, and no expunge that could take
        the messages another mail program flagged ``\\Deleted``."""
        server = self._server()

        move_message(self.account, self.msg, self.archive)

        self.assertEqual(
            server.named(*TRANSFER_COMMANDS), [("move", [SOURCE_UID], "Archive")]
        )
        self.assert_moved(server)
        self.assertEqual(server.uids("INBOX"), [OTHER_DELETED_UID])

    def test_with_uidplus_the_expunge_takes_the_moved_message_alone(self):
        """Issue #1513: a plain EXPUNGE also removed every other message of
        the mailbox flagged ``\\Deleted``."""
        server = self._server(capabilities=BARE | {"UIDPLUS"})

        copied = move_message(self.account, self.msg, self.archive)

        self.assertEqual(self._transfer(server), ["copy", "add_flags", "uid_expunge"])
        self.assertEqual(server.named("uid_expunge"), [("uid_expunge", [SOURCE_UID])])
        self.assertEqual(copied, COPY_UID)
        self.assert_moved(server)
        self.assertEqual(server.uids("INBOX"), [OTHER_DELETED_UID])
        self.assertEqual(server.flags("INBOX", OTHER_DELETED_UID), {r"\Deleted"})

    def test_returns_the_uid_the_server_gave_the_copy(self):
        server = self._server()

        self.assertEqual(move_message(self.account, self.msg, self.archive), COPY_UID)
        # Told by the COPYUID code, not looked up.
        self.assertEqual(server.named("search"), [])

    def test_without_uidplus_finds_the_copy_by_message_id(self):
        self.msg.message_id = MESSAGE_ID
        server = self._server(capabilities=BARE, message_id=MESSAGE_ID)

        self.assertEqual(move_message(self.account, self.msg, self.archive), COPY_UID)
        self.assertEqual(
            server.named("search"),
            [
                (
                    "search",
                    [
                        "UID",
                        f"{COPY_UID}:*",
                        "HEADER",
                        "Message-ID",
                        MESSAGE_ID.encode(),
                    ],
                )
            ],
        )

    def test_a_message_id_no_atom_may_hold_is_still_found(self):
        """Sent bare, ``x{20}`` announces a literal the server then waits
        for, and ``%``, ``*`` or ``]`` are answered BAD."""
        for message_id in ("<x{20}@example.com>", "<50%*off]@example.com>", "<(a)@b>"):
            with self.subTest(message_id):
                self.msg.message_id = message_id
                server = self._server(capabilities=BARE, message_id=message_id)

                self.assertEqual(
                    move_message(self.account, self.msg, self.archive), COPY_UID
                )
                (search,) = server.named("search")
                self.assertIsInstance(search[1][-1], literal)

    def test_a_connection_lost_before_the_move_is_dropped_and_raised(self):
        self.msg.message_id = MESSAGE_ID
        server = self._server(message_id=MESSAGE_ID)
        server.fail("status", IMAPClientAbortError("socket error: EOF"))

        with shared_connections():
            with self.assertRaises(IMAPClientAbortError):
                move_message(self.account, self.msg, self.archive)
            server.recover("status")
            # The next operation gets a connection of its own, not the dead one.
            move_message(self.account, self.msg, self.archive)

        self.assertEqual(len(server.connections), 2)
        self.assertTrue(server.connections[0].closed)

    def test_a_connection_lost_looking_for_the_copy_is_dropped_not_raised(self):
        """The message has moved by then: the lookup answers None, and the
        next operation of the block must not inherit the dead connection."""
        self.msg.message_id = MESSAGE_ID
        server = self._server(capabilities=BARE, message_id=MESSAGE_ID)
        server.fail("search", IMAPClientAbortError("socket error: EOF"))

        with shared_connections():
            self.assertIsNone(move_message(self.account, self.msg, self.archive))
            mark_read(self.account, self.msg)

        self.assertEqual(len(server.connections), 2)
        self.assertTrue(server.connections[0].closed)
        self.assertEqual(len(server.named("add_flags")), 2)

    def test_an_older_copy_below_uidnext_is_not_taken(self):
        """``100:*`` matches the highest UID even when it is below 100."""
        self.msg.message_id = MESSAGE_ID
        server = self.enterContext(fake_imap(capabilities=BARE))
        server.deliver("Archive", uid=64, message_id=MESSAGE_ID)
        # The Archive's counter is at 100 and the source is already gone, so
        # nothing is copied: the older copy is the highest UID there is.
        server.remove("Archive", server.deliver("Archive", uid=COPY_UID - 1))

        self.assertIsNone(move_message(self.account, self.msg, self.archive))
        self.assertEqual(len(server.named("search")), 1)
        self.assertEqual(server.uids("Archive"), [64])

    def test_two_candidate_copies_answer_none(self):
        self.msg.message_id = MESSAGE_ID
        server = self._server(capabilities=BARE, message_id=MESSAGE_ID)
        answer_status = FakeImapClient.folder_status

        def status_then_a_twin_arrives(client, folder, what=None):
            answer = answer_status(client, folder, what)
            server.deliver("Archive", message_id=MESSAGE_ID)
            return answer

        with patch.object(FakeImapClient, "folder_status", status_then_a_twin_arrives):
            copied = move_message(self.account, self.msg, self.archive)

        self.assertIsNone(copied)
        self.assertEqual(server.uids("Archive"), [COPY_UID - 1, COPY_UID, COPY_UID + 1])

    def test_a_failing_lookup_does_not_undo_a_completed_move(self):
        self.msg.message_id = MESSAGE_ID
        server = self._server(capabilities=BARE, message_id=MESSAGE_ID)
        server.fail("search", error=OSError("connection reset"))

        self.assertIsNone(move_message(self.account, self.msg, self.archive))
        self.assertEqual(len(server.named("expunge")), 1)
        self.assert_moved(server)

    def test_a_message_id_carrying_a_line_break_is_never_sent(self):
        self.msg.message_id = '<a@b>"\r\nA1 DELETE "INBOX'
        server = self._server(capabilities=BARE)

        self.assertIsNone(move_message(self.account, self.msg, self.archive))
        self.assertEqual(server.named("search"), [])
        self.assert_moved(server)

    def test_a_non_ascii_message_id_does_not_fail_a_completed_move(self):
        # A SEARCH argument outside ASCII raises on the way out - after the
        # source has already been expunged.
        self.msg.message_id = "<réunion@example.com>"
        server = self._server(capabilities=BARE)

        self.assertIsNone(move_message(self.account, self.msg, self.archive))
        self.assertEqual(len(server.named("expunge")), 1)
        self.assertEqual(server.named("search"), [])
        self.assert_moved(server)
