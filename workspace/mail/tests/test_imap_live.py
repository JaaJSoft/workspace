"""The mail services against a real IMAP server.

Skipped unless WORKSPACE_IMAP_HOST names a Dovecot server that accepts any
login with the password in WORKSPACE_IMAP_PASSWORD, which is how the
``dovecot/dovecot`` image behaves once USER_PASSWORD is set. The CI job
``imap-live`` runs two of them and also sets WORKSPACE_IMAP_TESTS=1, which
turns a missing server into a failure rather than a silent skip:

- WORKSPACE_IMAP_PORT (implicit TLS) and WORKSPACE_IMAP_STARTTLS_PORT reach
  the image as shipped: MOVE, UIDPLUS, CONDSTORE and the rest.
- WORKSPACE_IMAP_BARE_PORT reaches a second server whose configuration
  (``live/dovecot-bare.conf``) hides those extensions, for the paths a
  server without them takes.

Every test gets a mailbox of its own: a login nobody used before. A second
connection plays the other mail program, the one that delivers, flags and
deletes behind Workspace's back, and reads the server's state back.

To run it locally::

    docker run -d -p 31143:31143 -p 31993:31993 \
        -e USER_PASSWORD=secret dovecot/dovecot:2.4.5
    WORKSPACE_IMAP_HOST=localhost WORKSPACE_IMAP_PORT=31993 \
        WORKSPACE_IMAP_STARTTLS_PORT=31143 WORKSPACE_IMAP_PASSWORD=secret \
        uv run python manage.py test workspace.mail.tests.test_imap_live
"""

import functools
import os
import secrets
import ssl
import tempfile
import unittest
from email.message import EmailMessage
from itertools import batched
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from imapclient import IMAPClient

from workspace.common.booleans import is_truthy
from workspace.mail import tasks as mail_tasks
from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.drafts import save_composed_draft
from workspace.mail.services.imap_connection import connect_imap, test_imap_connection
from workspace.mail.services.imap_folders import (
    create_folder,
    delete_folder,
    move_folder,
    rename_folder,
)
from workspace.mail.services.imap_messages import append_to_sent, delete_draft
from workspace.mail.services.imap_sync import sync_account, sync_folder_messages
from workspace.mail.services.sending import deliver_email
from workspace.mail.services.smtp import SentMessage
from workspace.mail.services.triage import (
    EXPUNGED,
    TRASHED,
    mark_folder_read,
    move_to_folder,
    set_flag,
    trash_message,
)

HOST = os.environ.get("WORKSPACE_IMAP_HOST", "")
PORT = int(os.environ.get("WORKSPACE_IMAP_PORT") or 993)
STARTTLS_PORT = int(os.environ.get("WORKSPACE_IMAP_STARTTLS_PORT") or 0)
BARE_PORT = int(os.environ.get("WORKSPACE_IMAP_BARE_PORT") or 0)
PASSWORD = os.environ.get("WORKSPACE_IMAP_PASSWORD", "")
REQUIRED = is_truthy(os.environ.get("WORKSPACE_IMAP_TESTS"))

# One UID FETCH naming every message of a folder this size is longer than the
# 64 KB a Dovecot command line may be.
LARGE_FOLDER = 13_000

SEEN = b"\\Seen"
FLAGGED = b"\\Flagged"
ANSWERED = b"\\Answered"
DELETED = b"\\Deleted"
DRAFT = b"\\Draft"

User = get_user_model()

live = unittest.skipUnless(
    HOST or REQUIRED, "set WORKSPACE_IMAP_HOST to run the live IMAP tests"
)


@functools.cache
def _pinned_context(port):
    """A context trusting the certificate the test server signed for itself.

    Everything else is verified as in production, the host name included:
    the image's certificate names ``localhost``.
    """
    return ssl.create_default_context(cadata=ssl.get_server_certificate((HOST, port)))


def _mail(subject, body="Hello.", message_id=None):
    message = EmailMessage()
    message["From"] = "Sender <sender@example.com>"
    message["To"] = "me@example.com"
    message["Subject"] = subject
    message["Message-ID"] = message_id or f"<{secrets.token_hex(8)}@example.com>"
    message["Date"] = "Mon, 02 Nov 2026 09:30:00 +0000"
    message.set_content(body)
    return message.as_bytes()


class LiveImapCase(TestCase):
    port = PORT

    @classmethod
    def setUpClass(cls):
        if not (HOST and PASSWORD and cls.port):
            raise AssertionError(
                "WORKSPACE_IMAP_TESTS=1 but WORKSPACE_IMAP_HOST, "
                "WORKSPACE_IMAP_PASSWORD or the port of this server is not set."
            )
        super().setUpClass()
        cls.enterClassContext(
            patch(
                "workspace.mail.services.tls.client_context",
                side_effect=lambda: _pinned_context(cls.port),
            )
        )

    def setUp(self):
        self.addCleanup(cache.clear)
        self.login = f"ws-{secrets.token_hex(6)}"
        self.user = User.objects.create_user(username=self.login, password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email=f"{self.login}@example.com",
            display_name="Live Test",
            imap_host=HOST,
            imap_port=self.port,
            imap_use_ssl=True,
            smtp_host="smtp.invalid",
            username=self.login,
        )
        self.account.set_password(PASSWORD)
        self.account.save()
        self.remote = IMAPClient(
            HOST,
            self.port,
            ssl=True,
            ssl_context=_pinned_context(self.port),
            timeout=60,
        )
        self.remote.login(self.login, PASSWORD)
        self.addCleanup(self.remote.logout)

    # -- the other mail program -------------------------------------------

    def deliver(self, folder, subject, flags=(), **mail):
        """Put a message in `folder` and return the UID it got."""
        uid = self.remote.folder_status(folder, ["UIDNEXT"])[b"UIDNEXT"]
        self.remote.append(folder, _mail(subject, **mail), flags=flags)
        return uid

    def remote_fetch(self, folder, item):
        """``{uid: data}`` for every message the server holds in `folder`."""
        self.remote.select_folder(folder, readonly=True)
        fetched = {}
        # A few hundred UIDs a command: this client plays by the rules too.
        for uids in batched(self.remote.search("ALL"), 500, strict=False):
            fetched.update(self.remote.fetch(uids, [item]))
        return fetched

    def remote_flags(self, folder):
        """``{uid: flags}`` of every message the server holds in `folder`."""
        return {
            uid: set(data[b"FLAGS"]) - {b"\\Recent"}
            for uid, data in self.remote_fetch(folder, "FLAGS").items()
        }

    def remote_uids(self, folder):
        return sorted(self.remote_flags(folder))

    def remote_subjects(self, folder):
        return sorted(
            data[b"ENVELOPE"].subject.decode()
            for data in self.remote_fetch(folder, "ENVELOPE").values()
        )

    def remote_folders(self):
        return {name for _flags, _delimiter, name in self.remote.list_folders()}

    def flag_remotely(self, folder, uid, flag):
        self.remote.select_folder(folder)
        self.remote.add_flags([uid], [flag])

    # -- Workspace --------------------------------------------------------

    def sync(self):
        sync_account(self.account)
        self.account.refresh_from_db()
        self.assertEqual(self.account.last_sync_error, "")

    def folder(self, name):
        return MailFolder.objects.get(account=self.account, name=name)

    def message(self, folder, uid):
        return MailMessage.objects.select_related("account", "folder").get(
            folder=self.folder(folder), imap_uid=uid
        )

    def stored_uids(self, folder):
        return sorted(
            MailMessage.objects.filter(
                folder=self.folder(folder), deleted_at__isnull=True
            ).values_list("imap_uid", flat=True)
        )


class SyncCases:
    def test_folders_are_listed_with_their_type(self):
        self.sync()

        types = dict(
            MailFolder.objects.filter(account=self.account).values_list(
                "name", "folder_type"
            )
        )
        self.assertEqual(
            types,
            {
                "INBOX": "inbox",
                "Drafts": "drafts",
                "Sent": "sent",
                "Trash": "trash",
                "Junk": "spam",
            },
        )
        self.assertEqual(self.account.imap_delimiter, "/")

    def test_messages_come_in_with_their_flags(self):
        plain = self.deliver("INBOX", "Plain")
        read = self.deliver("INBOX", "Read", flags=[SEEN])
        starred = self.deliver("INBOX", "Starred", flags=[FLAGGED])
        answered = self.deliver("INBOX", "Answered", flags=[SEEN, ANSWERED])

        self.sync()

        self.assertEqual(self.stored_uids("INBOX"), [plain, read, starred, answered])
        self.assertEqual(self.message("INBOX", plain).subject, "Plain")
        self.assertFalse(self.message("INBOX", plain).is_read)
        self.assertFalse(self.message("INBOX", plain).is_answered)
        self.assertTrue(self.message("INBOX", read).is_read)
        self.assertTrue(self.message("INBOX", starred).is_starred)
        self.assertTrue(self.message("INBOX", answered).is_answered)
        inbox = self.folder("INBOX")
        self.assertEqual((inbox.message_count, inbox.unread_count), (4, 2))
        self.assertEqual(inbox.last_sync_uid, answered)

    def test_reading_a_message_leaves_it_unread_on_the_server(self):
        uid = self.deliver("INBOX", "Untouched")

        self.sync()

        self.assertEqual(self.remote_flags("INBOX"), {uid: set()})

    def test_a_later_pass_imports_only_what_is_new(self):
        first = self.deliver("INBOX", "First")
        self.sync()
        stored = self.message("INBOX", first)

        second = self.deliver("INBOX", "Second")
        self.sync()
        self.sync()

        self.assertEqual(self.stored_uids("INBOX"), [first, second])
        self.assertEqual(self.message("INBOX", first).pk, stored.pk)

    def test_flags_set_by_another_client_come_in(self):
        uid = self.deliver("INBOX", "Flagged elsewhere")
        other = self.deliver("INBOX", "Read here", flags=[SEEN, FLAGGED, ANSWERED])
        self.sync()

        self.flag_remotely("INBOX", uid, SEEN)
        self.flag_remotely("INBOX", uid, FLAGGED)
        self.flag_remotely("INBOX", uid, ANSWERED)
        self.remote.remove_flags([other], [SEEN, FLAGGED, ANSWERED])
        self.sync()

        message = self.message("INBOX", uid)
        self.assertTrue(message.is_read)
        self.assertTrue(message.is_starred)
        self.assertTrue(message.is_answered)
        message = self.message("INBOX", other)
        self.assertFalse(message.is_read)
        self.assertFalse(message.is_starred)
        self.assertFalse(message.is_answered)
        self.assertEqual(self.folder("INBOX").unread_count, 1)

    def test_a_message_deleted_by_another_client_goes_away(self):
        kept = self.deliver("INBOX", "Kept")
        gone = self.deliver("INBOX", "Gone")
        self.sync()

        self.flag_remotely("INBOX", gone, DELETED)
        self.remote.expunge()
        self.sync()

        self.assertEqual(self.stored_uids("INBOX"), [kept])
        self.assertIsNotNone(self.message("INBOX", gone).deleted_at)
        self.assertEqual(self.folder("INBOX").message_count, 1)

    def test_a_folder_created_again_is_imported_again(self):
        self.remote.create_folder("Projects")
        self.deliver("Projects", "Before")
        self.sync()
        self.assertEqual(len(self.stored_uids("Projects")), 1)

        self.remote.delete_folder("Projects")
        self.remote.create_folder("Projects")
        uid = self.deliver("Projects", "After")
        self.sync()

        self.assertEqual(self.stored_uids("Projects"), [uid])
        self.assertEqual(self.message("Projects", uid).subject, "After")

    def test_a_folder_named_with_accents_by_another_client_syncs(self):
        self.remote.create_folder("Événements & fêtes")
        uid = self.deliver("Événements & fêtes", "Invitation")

        self.sync()

        folder = MailFolder.objects.get(
            account=self.account, display_name="Événements & fêtes"
        )
        self.assertEqual(
            list(folder.messages.values_list("imap_uid", "subject")),
            [(uid, "Invitation")],
        )

    def test_a_removed_folder_goes_away(self):
        self.remote.create_folder("Old")
        self.sync()

        self.remote.delete_folder("Old")
        self.sync()

        self.assertFalse(
            MailFolder.objects.filter(account=self.account, name="Old").exists()
        )

    def test_a_pass_over_every_folder_logs_in_once(self):
        self.deliver("INBOX", "One")
        self.deliver("Sent", "Two")

        with patch(
            "workspace.mail.services.imap_connection.connect_imap", wraps=connect_imap
        ) as connect:
            self.sync()

        self.assertEqual(connect.call_count, 1)
        self.assertEqual(MailMessage.objects.filter(account=self.account).count(), 2)


class TriageCases:
    def test_a_flag_set_here_reaches_the_server(self):
        uid = self.deliver("INBOX", "Flag me")
        self.sync()
        message = self.message("INBOX", uid)

        self.assertTrue(set_flag(message, "read"))
        self.assertTrue(set_flag(message, "starred"))
        self.assertEqual(self.remote_flags("INBOX"), {uid: {SEEN, FLAGGED}})

        self.assertTrue(set_flag(message, "unread"))
        self.assertTrue(set_flag(message, "unstarred"))
        self.assertEqual(self.remote_flags("INBOX"), {uid: set()})

    def test_a_reply_sent_here_marks_its_parent_answered_on_the_server(self):
        uid = self.deliver("INBOX", "Answer me", flags=[SEEN])
        self.sync()
        parent = self.message("INBOX", uid)

        reply = tempfile.TemporaryFile()
        reply.write(b"Message-ID: <re@example.com>\r\nSubject: Re\r\n\r\nOK\r\n")
        with (
            patch(
                "workspace.mail.services.smtp.send_email",
                return_value=SentMessage(reply, "<re@example.com>", outgoing_start=0),
            ),
            patch.object(mail_tasks.sync_folder, "delay"),
        ):
            deliver_email(
                self.account,
                to=["bob@example.com"],
                subject="Re: Answer me",
                reply_message_id=parent.uuid,
            )

        self.assertEqual(self.remote_flags("INBOX"), {uid: {SEEN, ANSWERED}})
        parent.refresh_from_db()
        self.assertTrue(parent.is_answered)

    def test_marking_a_folder_read_flags_every_message(self):
        uids = [self.deliver("INBOX", f"Unread {n}") for n in range(3)]
        self.sync()

        updated, synced = mark_folder_read(self.folder("INBOX"))

        self.assertEqual((updated, synced), (3, True))
        self.assertEqual(self.remote_flags("INBOX"), dict.fromkeys(uids, {SEEN}))

    def test_a_moved_message_lands_in_the_target_and_the_row_follows(self):
        self.remote.create_folder("Archive")
        self.deliver("Archive", "Already there")
        uid = self.deliver("INBOX", "Move me", flags=[SEEN])
        stays = self.deliver("INBOX", "Stays")
        self.sync()
        message = self.message("INBOX", uid)

        move_to_folder(message, self.folder("Archive"))

        self.assertEqual(self.remote_uids("INBOX"), [stays])
        self.assertEqual(self.remote_subjects("Archive"), ["Already there", "Move me"])
        message.refresh_from_db()
        self.assertEqual(message.folder, self.folder("Archive"))
        self.assertIsNone(message.deleted_at)
        self.assertEqual(
            self.remote_flags("Archive")[message.imap_uid], {SEEN}, "the copy's UID"
        )

        self.sync()
        self.assertEqual(
            MailMessage.objects.filter(
                account=self.account, subject="Move me", deleted_at__isnull=True
            ).count(),
            1,
        )

    def test_a_message_id_no_atom_may_hold_does_not_stall_the_move(self):
        """Sent bare in the SEARCH that looks for the copy, ``{20}`` announces
        a literal the server waits for until the connection times out."""
        self.remote.create_folder("Archive")
        uid = self.deliver("INBOX", "Odd id", message_id="<x{20}%*]@example.com>")
        self.sync()
        message = self.message("INBOX", uid)

        move_to_folder(message, self.folder("Archive"))

        message.refresh_from_db()
        self.assertEqual(message.folder, self.folder("Archive"))
        self.assertIsNone(message.deleted_at)
        self.assertEqual(self.remote_uids("Archive"), [message.imap_uid])

    def test_a_deleted_message_goes_to_trash_then_away(self):
        uid = self.deliver("INBOX", "Delete me")
        self.sync()
        message = self.message("INBOX", uid)

        self.assertEqual(trash_message(message), TRASHED)

        self.assertEqual(self.remote_uids("INBOX"), [])
        self.assertEqual(self.remote_subjects("Trash"), ["Delete me"])
        message.refresh_from_db()
        self.assertEqual(message.folder, self.folder("Trash"))

        self.assertEqual(trash_message(message), EXPUNGED)

        self.assertEqual(self.remote_uids("Trash"), [])
        message.refresh_from_db()
        self.assertIsNotNone(message.deleted_at)

    def test_a_draft_is_saved_replaced_and_deleted(self):
        self.sync()

        draft = save_composed_draft(
            self.account, to=["you@example.com"], subject="First", body_text="One"
        )

        self.assertEqual(self.remote_subjects("Drafts"), ["First"])
        self.assertEqual(self.remote_flags("Drafts"), {draft.imap_uid: {DRAFT, SEEN}})
        self.assertTrue(draft.is_draft)

        replaced = save_composed_draft(
            self.account,
            to=["you@example.com"],
            subject="Second",
            body_text="Two",
            replace_draft_uuid=draft.uuid,
        )

        self.assertEqual(self.remote_subjects("Drafts"), ["Second"])
        self.assertEqual(self.stored_uids("Drafts"), [replaced.imap_uid])

        delete_draft(self.account, replaced)

        self.assertEqual(self.remote_uids("Drafts"), [])
        self.assertEqual(self.stored_uids("Drafts"), [])

    def test_a_sent_copy_is_filed_once(self):
        self.sync()
        raw = _mail("Sent copy", message_id="<sent-copy@example.com>").replace(
            b"\r\n", b"\n"
        )

        for _attempt in range(2):
            with tempfile.TemporaryFile() as message_file:
                message_file.write(raw.replace(b"\n", b"\r\n"))
                append_to_sent(self.account, message_file, "<sent-copy@example.com>")

        self.assertEqual(self.remote_subjects("Sent"), ["Sent copy"])
        (flags,) = self.remote_flags("Sent").values()
        self.assertEqual(flags, {SEEN})

        sync_folder_messages(self.account, self.folder("Sent"))
        (stored,) = MailMessage.objects.filter(folder=self.folder("Sent"))
        self.assertEqual(stored.message_id, "<sent-copy@example.com>")
        self.assertTrue(stored.is_read)


class FolderCases:
    def test_folders_are_created_renamed_moved_and_deleted(self):
        self.sync()

        parent = create_folder(self.account, "Clients")
        child = create_folder(self.account, "Acme Corp", parent_name=parent.name)
        self.assertLessEqual({"Clients", "Clients/Acme Corp"}, self.remote_folders())

        rename_folder(self.account, child, "Acme")
        self.assertIn("Clients/Acme", self.remote_folders())
        self.assertNotIn("Clients/Acme Corp", self.remote_folders())

        move_folder(self.account, child, "")
        self.assertIn("Acme", self.remote_folders())
        child.refresh_from_db()
        self.assertEqual(child.name, "Acme")

        delete_folder(self.account, child)
        self.assertNotIn("Acme", self.remote_folders())

        self.sync()
        self.assertEqual(
            set(
                MailFolder.objects.filter(
                    account=self.account, folder_type="other"
                ).values_list("name", flat=True)
            ),
            {"Clients"},
        )


@live
class LiveImapTests(SyncCases, TriageCases, FolderCases, LiveImapCase):
    """The image as shipped: MOVE, UIDPLUS, CONDSTORE, SPECIAL-USE."""

    @unittest.skipUnless(
        STARTTLS_PORT or REQUIRED, "set WORKSPACE_IMAP_STARTTLS_PORT for STARTTLS"
    )
    def test_both_ways_of_securing_the_connection_log_in(self):
        self.assertEqual(test_imap_connection(self.account), (True, None))

        self.account.imap_port = STARTTLS_PORT
        self.account.imap_use_ssl = False
        self.assertEqual(test_imap_connection(self.account), (True, None))

    def test_a_wrong_password_is_reported(self):
        self.account.set_password("not the password")

        ok, error = test_imap_connection(self.account)

        self.assertFalse(ok)
        self.assertTrue(error)

    def test_a_pass_over_unchanged_mailboxes_reads_none_of_them(self):
        uid = self.deliver("INBOX", "One")
        self.sync()

        with (
            patch.object(IMAPClient, "search", side_effect=AssertionError("SEARCH")),
            patch.object(IMAPClient, "fetch", side_effect=AssertionError("FETCH")),
        ):
            self.sync()

        self.flag_remotely("INBOX", uid, SEEN)
        self.sync()
        self.assertTrue(self.message("INBOX", uid).is_read)

    # -- #1513 --------------------------------------------------------------

    def test_a_move_leaves_the_messages_another_client_marked_deleted(self):
        self.remote.create_folder("Archive")
        marked = self.deliver("INBOX", "Marked deleted elsewhere", flags=[DELETED])
        uid = self.deliver("INBOX", "Move me")
        self.sync()

        move_to_folder(self.message("INBOX", uid), self.folder("Archive"))

        self.assertEqual(self.remote_flags("INBOX"), {marked: {DELETED}})

    def test_a_delete_leaves_the_messages_another_client_marked_deleted(self):
        marked = self.deliver("Trash", "Marked deleted elsewhere", flags=[DELETED])
        uid = self.deliver("Trash", "Delete me")
        self.sync()

        self.assertEqual(trash_message(self.message("Trash", uid)), EXPUNGED)

        self.assertEqual(self.remote_flags("Trash"), {marked: {DELETED}})

    def test_replacing_a_draft_leaves_the_drafts_marked_deleted(self):
        marked = self.deliver("Drafts", "Marked deleted elsewhere", flags=[DELETED])
        self.sync()
        draft = save_composed_draft(
            self.account, to=["you@example.com"], subject="First", body_text="One"
        )

        replaced = save_composed_draft(
            self.account,
            to=["you@example.com"],
            subject="Second",
            body_text="Two",
            replace_draft_uuid=draft.uuid,
        )
        delete_draft(self.account, replaced)

        self.assertEqual(self.remote_flags("Drafts"), {marked: {DELETED}})

    # -- #1759 --------------------------------------------------------------

    def test_folders_with_accented_names_are_created_and_renamed(self):
        self.sync()

        folder = create_folder(self.account, "Réunions")
        child = create_folder(self.account, "Été & hiver", parent_name=folder.name)
        self.assertLessEqual(
            {"Réunions", "Réunions/Été & hiver"}, self.remote_folders()
        )
        self.assertEqual(child.display_name, "Été & hiver")

        rename_folder(self.account, child, "日本語")
        self.assertIn("Réunions/日本語", self.remote_folders())

        uid = self.deliver("Réunions", "Ordre du jour")
        self.sync()

        folder.refresh_from_db()
        self.assertEqual(folder.display_name, "Réunions")
        self.assertEqual(
            list(folder.messages.values_list("imap_uid", flat=True)), [uid]
        )
        self.assertEqual(
            set(
                MailFolder.objects.filter(
                    account=self.account, folder_type="other"
                ).values_list("display_name", flat=True)
            ),
            {"Réunions", "日本語"},
        )

    # -- #1744 --------------------------------------------------------------

    def test_a_folder_of_thousands_of_messages_keeps_syncing(self):
        first = self.deliver("INBOX", "First")
        self.sync()
        self.remote.multiappend(
            "INBOX", [_mail(f"Bulk {n}", body="x") for n in range(LARGE_FOLDER)]
        )
        self.sync()
        self.assertEqual(len(self.stored_uids("INBOX")), LARGE_FOLDER + 1)

        self.flag_remotely("INBOX", first, SEEN)
        self.flag_remotely("INBOX", first + 5000, FLAGGED)
        new = self.deliver("INBOX", "After the bulk")
        self.sync()

        self.assertTrue(self.message("INBOX", first).is_read)
        self.assertTrue(self.message("INBOX", first + 5000).is_starred)
        self.assertEqual(self.message("INBOX", new).subject, "After the bulk")
        inbox = self.folder("INBOX")
        self.assertEqual(inbox.last_sync_uid, new)
        self.assertEqual(inbox.message_count, LARGE_FOLDER + 2)
        self.assertEqual(inbox.unread_count, LARGE_FOLDER + 1)

        updated, synced = mark_folder_read(inbox)

        self.assertEqual((updated, synced), (LARGE_FOLDER + 1, True))
        flags = self.remote_flags("INBOX")
        self.assertEqual(len(flags), LARGE_FOLDER + 2)
        self.assertTrue(all(SEEN in message_flags for message_flags in flags.values()))


@live
@unittest.skipUnless(
    BARE_PORT or REQUIRED, "set WORKSPACE_IMAP_BARE_PORT for the bare server"
)
class LiveImapWithoutExtensionsTests(SyncCases, TriageCases, FolderCases, LiveImapCase):
    """A server offering neither MOVE, UIDPLUS nor CONDSTORE."""

    port = BARE_PORT

    def test_the_server_hides_its_extensions(self):
        for capability in ("MOVE", "UIDPLUS", "CONDSTORE", "QRESYNC"):
            self.assertFalse(self.remote.has_capability(capability), capability)
