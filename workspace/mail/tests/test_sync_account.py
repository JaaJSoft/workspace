"""Regression tests for workspace.mail.services.imap_sync.sync_account.

Specifically pin down that per-folder errors are surfaced via
account.last_sync_error rather than being silently masked by the final clear,
and that a pass shares one connection between its folders.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_sync import sync_account
from workspace.mail.tests.imap_fake import fake_imap

User = get_user_model()


class SyncAccountMixin:
    def setUp(self):
        self.user = User.objects.create_user(username="syncuser", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="user@example.com",
            last_sync_error="",
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

    def _imported(self, folder):
        return sorted(
            MailMessage.objects.filter(folder=folder).values_list("imap_uid", flat=True)
        )


class SyncAccountErrorReportingTests(SyncAccountMixin, TestCase):
    def test_clears_error_when_all_folders_succeed(self):
        self.account.last_sync_error = "previous error"
        self.account.save(update_fields=["last_sync_error"])

        with fake_imap() as server:
            server.mailbox("Archive")
            sync_account(self.account)

        self.account.refresh_from_db()
        self.assertEqual(self.account.last_sync_error, "")
        self.assertIsNotNone(self.account.last_sync_at)

    def test_records_error_when_a_folder_fails(self):
        with fake_imap() as server:
            # Archive is synced first and is the only folder with something
            # to fetch: the inbox going through after it must not clear the
            # error.
            server.deliver("Archive")
            server.fail("fetch")
            sync_account(self.account)

        self.account.refresh_from_db()
        self.assertNotEqual(
            self.account.last_sync_error,
            "",
            "last_sync_error must not be cleared when a folder sync fails",
        )
        self.assertIsNotNone(self.account.last_sync_at)
        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.uid_validity, server.mailbox("INBOX").uidvalidity)


class SyncAccountConnectionTests(SyncAccountMixin, TestCase):
    """A pass logs in once, whatever the number of folders."""

    def test_one_connection_serves_the_folder_list_and_every_folder(self):
        with fake_imap() as server:
            server.deliver("INBOX")
            server.deliver("Archive")
            server.deliver("Sent")
            sync_account(self.account)

        self.assertEqual(len(server.connections), 1)
        self.assertEqual(len(server.named("logout")), 1)
        self.assertEqual(
            [command[1] for command in server.named("select")],
            ["Archive", "INBOX", "Sent"],
        )
        self.assertEqual(MailMessage.objects.filter(account=self.account).count(), 3)

    def test_a_pass_over_unchanged_mailboxes_only_selects_them(self):
        with fake_imap() as server:
            server.deliver("INBOX")
            server.deliver("Archive")
            sync_account(self.account)
            first_pass = len(server.commands)

            sync_account(self.account)

        self.assertEqual(
            server.commands[first_pass:],
            [
                ("list",),
                ("select", "Archive", True),
                ("select", "INBOX", True),
                ("logout",),
            ],
        )
        self.assertEqual(len(server.connections), 2)

    def test_a_lost_connection_is_replaced_for_the_next_folder(self):
        with fake_imap() as server:
            server.deliver("Archive")
            uid = server.deliver("INBOX")
            server.fail("fetch", error=OSError("connection reset"), times=1)
            sync_account(self.account)

        lost, replacement = server.connections
        self.assertTrue(lost.closed)
        self.assertTrue(replacement.closed)
        self.assertEqual(self._imported(self.archive), [])
        self.assertEqual(self._imported(self.inbox), [uid])
        self.account.refresh_from_db()
        self.assertNotEqual(self.account.last_sync_error, "")
