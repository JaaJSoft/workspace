"""Regression tests for workspace.mail.services.imap_messages.save_draft.

The critical invariant: an existing draft (old_uid) must not be deleted on the
IMAP server until the new draft has been APPENDed successfully. Otherwise a
network/quota failure mid-save would lose both copies.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_messages import DELETED, DRAFT, SEEN, save_draft
from workspace.mail.tests.imap_fake import (
    BARE,
    FakeImapClient,
    fake_imap,
    raw_message,
)

User = get_user_model()

RAW_DRAFT = (
    b"From: a@example.com\r\n"
    b"To: b@example.com\r\n"
    b"Subject: hi\r\n"
    b"Message-ID: <our-draft-id@example.com>\r\n"
    b"\r\nbody"
)


class SaveDraftTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="draftuser", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.drafts = MailFolder.objects.create(
            account=self.account,
            name="Drafts",
            display_name="Drafts",
            folder_type=MailFolder.FolderType.DRAFTS,
        )

    def test_append_failure_leaves_old_draft_intact(self):
        """If APPEND fails, the old draft must NOT be flagged deleted nor
        expunged; otherwise the user loses both old and new drafts."""
        with fake_imap() as server:
            old = server.deliver("Drafts", subject="Old draft")
            server.fail("append")

            result = save_draft(self.account, RAW_DRAFT, old_uid=old)

        self.assertIsNone(result)
        self.assertEqual(server.uids("Drafts"), [old])
        self.assertEqual(server.flags("Drafts", old), set())

    def test_a_lost_connection_propagates_and_leaves_old_draft_intact(self):
        with fake_imap() as server:
            old = server.deliver("Drafts", subject="Old draft")
            server.fail("append", OSError("connection reset"))

            with self.assertRaises(OSError):
                save_draft(self.account, RAW_DRAFT, old_uid=old)

        self.assertEqual(server.uids("Drafts"), [old])

    def test_append_success_then_deletes_old_draft(self):
        """On APPEND success with old_uid, the old draft is expunged, after
        the APPEND, and the new one carries the draft flags."""
        with fake_imap() as server:
            old = server.deliver("Drafts", subject="Old draft")

            result = save_draft(self.account, RAW_DRAFT, old_uid=old)

        (new,) = server.uids("Drafts")
        self.assertNotEqual(new, old)
        self.assertEqual(server.flags("Drafts", new), {DRAFT, SEEN})
        self.assertEqual(
            [command[0] for command in server.named("append", "uid_expunge")],
            ["append", "uid_expunge"],
        )
        self.assertEqual(result.imap_uid, new)
        self.assertTrue(result.is_draft)

    def test_replacing_a_draft_keeps_the_drafts_another_client_marked_deleted(self):
        """The expunge names the old draft: a plain EXPUNGE would also take
        every draft another mail program flagged deleted and kept."""
        with fake_imap() as server:
            marked = server.deliver("Drafts", subject="Marked", flags=[DELETED])
            old = server.deliver("Drafts", subject="Old draft")

            result = save_draft(self.account, RAW_DRAFT, old_uid=old)

        self.assertEqual(server.uids("Drafts"), [marked, result.imap_uid])

    def test_without_uidplus_the_old_draft_is_still_deleted(self):
        with fake_imap(capabilities=BARE) as server:
            old = server.deliver("Drafts", subject="Old draft")

            result = save_draft(self.account, RAW_DRAFT, old_uid=old)

        self.assertEqual(server.uids("Drafts"), [result.imap_uid])
        self.assertEqual(len(server.named("expunge")), 1)

    def test_no_old_uid_skips_delete(self):
        """A first-time draft save (no old_uid) expunges nothing."""
        with fake_imap() as server:
            server.deliver("Drafts", subject="Another draft")

            save_draft(self.account, RAW_DRAFT, old_uid=None)

        self.assertEqual(len(server.uids("Drafts")), 2)
        self.assertEqual(server.named("add_flags", "expunge", "uid_expunge"), [])

    def test_the_save_and_its_sync_share_one_connection(self):
        with fake_imap() as server:
            old = server.deliver("Drafts", subject="Old draft")

            save_draft(self.account, RAW_DRAFT, old_uid=old)

        self.assertEqual(len(server.connections), 1)

    def test_returns_appended_draft_not_concurrent_intruder(self):
        """If a parallel IMAP session APPENDed a different draft and the sync
        picked both up, we must return OUR draft (matched by Message-ID), not
        the intruder, even when the intruder is the most recent."""
        append = FakeImapClient.append

        def append_then_intrude(client, *args, **kwargs):
            # The intruder lands after our APPEND, before our sync reads the
            # folder: exactly where "the newest draft" would return it.
            append(client, *args, **kwargs)
            client.server.deliver(
                "Drafts",
                raw=raw_message(message_id="<intruder@other-client.example.com>"),
            )

        with (
            fake_imap() as server,
            patch.object(FakeImapClient, "append", append_then_intrude),
        ):
            server.mailbox("Drafts")

            result = save_draft(self.account, RAW_DRAFT, old_uid=None)

        self.assertEqual(
            MailMessage.objects.filter(folder=self.drafts).count(),
            2,
            "the sync imported both drafts",
        )
        self.assertEqual(result.message_id, "<our-draft-id@example.com>")
