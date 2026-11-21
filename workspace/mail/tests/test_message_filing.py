"""Archive, Report spam and Not spam, down to the mailboxes on the server.

All three go through `move_to_folder`, so the row follows the message to
the UID the server gave it; what they add is which folder that is, per
account, and what to do on an account that has no such folder.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_sync import sync_folders
from workspace.mail.tests.imap_fake import FULL, fake_imap

User = get_user_model()

URL = "/api/v1/mail/messages/batch-action"


def make_account(owner, email="user@example.com", **fields):
    account = MailAccount.objects.create(
        owner=owner,
        email=email,
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username=email,
        **fields,
    )
    account.set_password("secret")
    account.save()
    return account


def make_folder(account, name, folder_type):
    return MailFolder.objects.create(
        account=account, name=name, display_name=name, folder_type=folder_type
    )


class FilingTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="filer", password="pass")
        self.account = make_account(self.user)
        self.inbox = make_folder(self.account, "INBOX", "inbox")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def deliver(self, server, folder, uid):
        server.deliver(folder.name, uid=uid)
        return MailMessage.objects.create(
            account=folder.account, folder=folder, imap_uid=uid
        )

    def post(self, action, *messages):
        return self.client.post(
            URL,
            {"message_ids": [str(m.uuid) for m in messages], "action": action},
            format="json",
        )


class ArchiveTests(FilingTestCase):
    def test_moves_the_message_into_the_archive_folder(self):
        archive = make_folder(self.account, "Archive", "archive")
        server = self.enterContext(fake_imap())
        server.mailbox("Archive")
        msg = self.deliver(server, self.inbox, 42)

        resp = self.post("archive", msg)

        self.assertEqual(resp.data, {"processed": 1})
        self.assertEqual(server.uids("INBOX"), [])
        self.assertEqual(len(server.uids("Archive")), 1)
        msg.refresh_from_db()
        self.assertEqual(msg.folder_id, archive.pk)
        self.assertEqual(msg.imap_uid, server.uids("Archive")[0])
        self.assertIsNone(msg.deleted_at)

    def test_refused_move_leaves_the_message_in_the_inbox(self):
        make_folder(self.account, "Archive", "archive")
        server = self.enterContext(fake_imap())
        server.mailbox("Archive")
        msg = self.deliver(server, self.inbox, 42)
        server.fail("move")

        resp = self.post("archive", msg)

        self.assertEqual(resp.data, {"processed": 0})
        self.assertEqual(server.uids("INBOX"), [42])
        msg.refresh_from_db()
        self.assertEqual((msg.folder_id, msg.imap_uid), (self.inbox.pk, 42))

    def test_creates_the_archive_folder_when_the_account_has_none(self):
        server = self.enterContext(fake_imap())
        msg = self.deliver(server, self.inbox, 42)

        resp = self.post("archive", msg)

        self.assertEqual(resp.data, {"processed": 1})
        archive = MailFolder.objects.get(account=self.account, name="Archive")
        self.assertEqual(archive.folder_type, "archive")
        self.assertEqual(len(server.uids("Archive")), 1)
        msg.refresh_from_db()
        self.assertEqual(msg.folder_id, archive.pk)

    def test_creates_it_once_for_a_whole_selection(self):
        server = self.enterContext(fake_imap())
        first = self.deliver(server, self.inbox, 42)
        second = self.deliver(server, self.inbox, 43)

        resp = self.post("archive", first, second)

        self.assertEqual(resp.data, {"processed": 2})
        self.assertEqual(len(server.named("create")), 1)
        self.assertEqual(len(server.uids("Archive")), 2)

    def test_creates_it_under_the_personal_namespace(self):
        server = self.enterContext(
            fake_imap(
                capabilities=FULL | {"NAMESPACE"},
                delimiter=".",
                personal_prefix="INBOX.",
            )
        )
        msg = self.deliver(server, self.inbox, 42)

        self.post("archive", msg)

        self.assertIn("INBOX.Archive", server.mailboxes)
        msg.refresh_from_db()
        self.assertEqual(msg.folder.name, "INBOX.Archive")
        self.assertEqual(msg.folder.display_name, "Archive")

    def test_takes_up_a_mailbox_the_server_already_has(self):
        # Created by a concurrent archive, or by another mail program, since
        # the last folder sync.
        server = self.enterContext(fake_imap())
        server.mailbox("Archive")
        msg = self.deliver(server, self.inbox, 42)

        resp = self.post("archive", msg)

        self.assertEqual(resp.data, {"processed": 1})
        self.assertEqual(len(server.uids("Archive")), 1)
        msg.refresh_from_db()
        self.assertEqual(msg.folder.name, "Archive")

    def test_leaves_what_has_nowhere_to_go(self):
        archive = make_folder(self.account, "Archive", "archive")
        trash = make_folder(self.account, "Trash", "trash")
        spam = make_folder(self.account, "Junk", "spam")
        drafts = make_folder(self.account, "Drafts", "drafts")
        server = self.enterContext(fake_imap())
        messages = [
            self.deliver(server, folder, 10 + i)
            for i, folder in enumerate([archive, trash, spam, drafts])
        ]

        resp = self.post("archive", *messages)

        self.assertEqual(resp.data, {"processed": 0})
        self.assertEqual(server.named("move", "copy", "create"), [])

    def test_a_selection_across_accounts_goes_to_each_ones_archive(self):
        other = make_account(self.user, email="other@example.com")
        other_inbox = make_folder(other, "INBOX", "inbox")
        own_archive = make_folder(self.account, "Archive", "archive")
        other_archive = make_folder(other, "Archive", "archive")
        server = self.enterContext(fake_imap())
        server.mailbox("Archive")
        first = self.deliver(server, self.inbox, 42)
        second = MailMessage.objects.create(
            account=other, folder=other_inbox, imap_uid=43
        )
        server.deliver("INBOX", uid=43)

        resp = self.post("archive", first, second)

        self.assertEqual(resp.data, {"processed": 2})
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.folder_id, own_archive.pk)
        self.assertEqual(second.folder_id, other_archive.pk)

    def test_ignores_another_users_message(self):
        stranger = User.objects.create_user(username="stranger", password="pass")
        account = make_account(stranger, email="stranger@example.com")
        inbox = make_folder(account, "INBOX", "inbox")
        server = self.enterContext(fake_imap())
        msg = self.deliver(server, inbox, 42)

        resp = self.post("archive", msg)

        self.assertEqual(resp.data, {"processed": 0})
        self.assertEqual(server.uids("INBOX"), [42])


class ArchiveIntoAllMailTests(FilingTestCase):
    """A server keeping every message in All Mail (Gmail), which is not synced."""

    def setUp(self):
        super().setUp()
        self.account.archives_in_all_mail = True
        self.account.save(update_fields=["archives_in_all_mail"])

    def test_takes_the_message_out_of_the_inbox(self):
        server = self.enterContext(fake_imap())
        msg = self.deliver(server, self.inbox, 42)

        resp = self.post("archive", msg)

        self.assertEqual(resp.data, {"processed": 1})
        self.assertEqual(server.uids("INBOX"), [])
        self.assertEqual(server.named("create"), [])
        msg.refresh_from_db()
        self.assertIsNotNone(msg.deleted_at)

    def test_refused_expunge_leaves_the_row(self):
        server = self.enterContext(fake_imap())
        msg = self.deliver(server, self.inbox, 42)
        server.fail("uid_expunge")

        resp = self.post("archive", msg)

        self.assertEqual(resp.data, {"processed": 0})
        msg.refresh_from_db()
        self.assertIsNone(msg.deleted_at)

    def test_leaves_a_message_outside_the_inbox(self):
        label = make_folder(self.account, "Projects", "other")
        server = self.enterContext(fake_imap())
        msg = self.deliver(server, label, 42)

        resp = self.post("archive", msg)

        self.assertEqual(resp.data, {"processed": 0})
        self.assertEqual(server.uids("Projects"), [42])
        msg.refresh_from_db()
        self.assertIsNone(msg.deleted_at)

    def test_an_archive_folder_still_comes_first(self):
        archive = make_folder(self.account, "Archive", "archive")
        server = self.enterContext(fake_imap())
        server.mailbox("Archive")
        msg = self.deliver(server, self.inbox, 42)

        self.post("archive", msg)

        msg.refresh_from_db()
        self.assertEqual(msg.folder_id, archive.pk)
        self.assertIsNone(msg.deleted_at)


class SpamTests(FilingTestCase):
    def setUp(self):
        super().setUp()
        self.spam = make_folder(self.account, "Junk", "spam")

    def test_report_spam_moves_the_message_into_spam(self):
        server = self.enterContext(fake_imap())
        server.mailbox("Junk")
        msg = self.deliver(server, self.inbox, 42)

        resp = self.post("spam", msg)

        self.assertEqual(resp.data, {"processed": 1})
        self.assertEqual(len(server.uids("Junk")), 1)
        msg.refresh_from_db()
        self.assertEqual(msg.folder_id, self.spam.pk)

    def test_not_spam_moves_it_back_to_the_inbox(self):
        server = self.enterContext(fake_imap())
        msg = self.deliver(server, self.spam, 42)

        resp = self.post("not_spam", msg)

        self.assertEqual(resp.data, {"processed": 1})
        self.assertEqual(server.uids("Junk"), [])
        self.assertEqual(len(server.uids("INBOX")), 1)
        msg.refresh_from_db()
        self.assertEqual(msg.folder_id, self.inbox.pk)

    def test_not_spam_leaves_a_message_outside_spam(self):
        server = self.enterContext(fake_imap())
        msg = self.deliver(server, self.inbox, 42)

        resp = self.post("not_spam", msg)

        self.assertEqual(resp.data, {"processed": 0})
        self.assertEqual(server.named("move", "copy"), [])

    def test_report_spam_leaves_what_it_does_not_apply_to(self):
        trash = make_folder(self.account, "Trash", "trash")
        sent = make_folder(self.account, "Sent", "sent")
        server = self.enterContext(fake_imap())
        messages = [
            self.deliver(server, folder, 10 + i)
            for i, folder in enumerate([self.spam, trash, sent])
        ]

        resp = self.post("spam", *messages)

        self.assertEqual(resp.data, {"processed": 0})
        self.assertEqual(server.named("move", "copy"), [])

    def test_report_spam_on_an_account_without_spam_folder(self):
        self.spam.delete()
        server = self.enterContext(fake_imap())
        msg = self.deliver(server, self.inbox, 42)

        resp = self.post("spam", msg)

        self.assertEqual(resp.data, {"processed": 0})
        self.assertEqual(server.named("move", "copy", "create"), [])


class AllMailDetectionTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="sync", password="pass")
        self.account = make_account(user)

    def test_sync_records_an_all_mail_mailbox(self):
        server = self.enterContext(fake_imap())
        server.mailbox("[Gmail]/All Mail", attributes=["\\HasNoChildren", "\\All"])

        sync_folders(self.account)

        self.account.refresh_from_db()
        self.assertTrue(self.account.archives_in_all_mail)
        # Still not synced: it holds a copy of every message.
        self.assertFalse(MailFolder.objects.filter(name="[Gmail]/All Mail").exists())

    def test_sync_clears_it_once_the_mailbox_is_gone(self):
        self.account.archives_in_all_mail = True
        self.account.save(update_fields=["archives_in_all_mail"])
        self.enterContext(fake_imap())

        sync_folders(self.account)

        self.account.refresh_from_db()
        self.assertFalse(self.account.archives_in_all_mail)
