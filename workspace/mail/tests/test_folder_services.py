from django.contrib.auth import get_user_model
from django.test import TestCase
from imapclient.exceptions import IMAPClientError

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap_folders import (
    create_folder,
    delete_folder,
    move_folder,
    rename_folder,
)
from workspace.mail.tests.imap_fake import fake_imap

User = get_user_model()


class IMAPFolderServiceMixin:
    """Common setup for IMAP folder service tests."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="imapuser",
            email="imap@test.com",
            password="pass123",
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email="user@example.com",
            imap_host="imap.example.com",
            imap_use_ssl=True,
            smtp_host="smtp.example.com",
            username="user@example.com",
        )
        self.account.set_password("secret")
        self.account.save()
        self.server = self.enterContext(fake_imap())

    def _folder(self, name, display_name=None):
        """A folder the server and the database both hold."""
        self.server.mailbox(name)
        return MailFolder.objects.create(
            account=self.account,
            name=name,
            display_name=display_name or name,
            folder_type="other",
        )

    def _names(self):
        """The stored name and the label of every folder of the account."""
        folders = MailFolder.objects.filter(account=self.account)
        return dict(folders.values_list("name", "display_name"))

    def assert_one_connection_closed(self):
        (connection,) = self.server.connections
        self.assertTrue(connection.closed)


class CreateFolderTests(IMAPFolderServiceMixin, TestCase):
    """Tests for create_folder IMAP service."""

    def test_create_folder_success(self):
        folder = create_folder(self.account, "Projects")

        self.assertIn("Projects", self.server.mailboxes)
        self.assert_one_connection_closed()
        self.assertIsNotNone(folder)
        self.assertEqual(folder.name, "Projects")
        self.assertEqual(folder.display_name, "Projects")
        self.assertEqual(folder.folder_type, "other")
        self.assertEqual(folder.account, self.account)
        self.assertTrue(
            MailFolder.objects.filter(account=self.account, name="Projects").exists()
        )

    def test_create_folder_with_hierarchy(self):
        folder = create_folder(self.account, "Work/Projects")

        self.assertIn("Work/Projects", self.server.mailboxes)
        self.assertEqual(folder.name, "Work/Projects")
        self.assertEqual(folder.display_name, "Projects")  # last segment

    def test_create_folder_under_a_parent(self):
        self.account.imap_delimiter = "."
        self.server.mailbox("INBOX.Work")

        folder = create_folder(self.account, "Projects", parent_name="INBOX.Work")

        self.assertIn("INBOX.Work.Projects", self.server.mailboxes)
        self.assertEqual(folder.name, "INBOX.Work.Projects")
        self.assertEqual(folder.display_name, "Projects")

    def test_a_non_ascii_name_goes_out_in_modified_utf7(self):
        """Issue #1759: the name was sent as typed, which the server refuses
        or files under a name no other mail program can read."""
        folder = create_folder(self.account, "Réunions")

        self.assertEqual(self.server.named("create"), [("create", "R&AOk-unions")])
        self.assertIn("R&AOk-unions", self.server.mailboxes)
        self.assertEqual(folder.name, "R&AOk-unions")
        self.assertEqual(folder.display_name, "Réunions")

    def test_an_ampersand_in_a_name_is_escaped(self):
        """A bare ``&`` opens an encoded sequence in modified UTF-7."""
        folder = create_folder(self.account, "R&D")

        self.assertIn("R&-D", self.server.mailboxes)
        self.assertEqual(folder.name, "R&-D")
        self.assertEqual(folder.display_name, "R&D")

    def test_the_parent_keeps_its_wire_form(self):
        self.server.mailbox("R&AOk-unions")

        folder = create_folder(self.account, "Été & co", parent_name="R&AOk-unions")

        self.assertIn("R&AOk-unions/&AMk-t&AOk- &- co", self.server.mailboxes)
        self.assertEqual(folder.name, "R&AOk-unions/&AMk-t&AOk- &- co")
        self.assertEqual(folder.display_name, "Été & co")

    def test_create_folder_imap_failure(self):
        self.server.mailbox("Existing")

        with self.assertRaises(IMAPClientError):
            create_folder(self.account, "Existing")

        # Should not have created local folder
        self.assertFalse(
            MailFolder.objects.filter(account=self.account, name="Existing").exists()
        )
        self.assert_one_connection_closed()

    def test_create_folder_closes_the_connection_on_error(self):
        self.server.fail("create", error=OSError("Connection lost"))

        with self.assertRaises(OSError):
            create_folder(self.account, "BadFolder")

        self.assertFalse(MailFolder.objects.filter(account=self.account).exists())
        self.assert_one_connection_closed()


class DeleteFolderTests(IMAPFolderServiceMixin, TestCase):
    """Tests for delete_folder IMAP service."""

    def setUp(self):
        super().setUp()
        self.folder = self._folder("ToDelete")
        # Add some messages to this folder
        for i in range(3):
            MailMessage.objects.create(
                account=self.account,
                folder=self.folder,
                imap_uid=200 + i,
            )

    def test_delete_folder_success(self):
        folder_uuid = self.folder.uuid

        delete_folder(self.account, self.folder)

        self.assertNotIn("ToDelete", self.server.mailboxes)
        self.assert_one_connection_closed()
        self.assertFalse(MailFolder.objects.filter(uuid=folder_uuid).exists())
        # Messages should also be deleted (cascade)
        self.assertFalse(MailMessage.objects.filter(folder_id=folder_uuid).exists())

    def test_delete_folder_with_a_non_ascii_name(self):
        folder = self._folder("R&AOk-unions", "Réunions")

        delete_folder(self.account, folder)

        self.assertEqual(self.server.named("delete"), [("delete", "R&AOk-unions")])
        self.assertNotIn("R&AOk-unions", self.server.mailboxes)

    def test_delete_folder_imap_failure(self):
        self.server.fail("delete")

        with self.assertRaises(IMAPClientError):
            delete_folder(self.account, self.folder)

        # Folder should still exist locally
        self.assertTrue(MailFolder.objects.filter(uuid=self.folder.uuid).exists())
        self.assertEqual(MailMessage.objects.filter(folder=self.folder).count(), 3)
        self.assertIn("ToDelete", self.server.mailboxes)
        self.assert_one_connection_closed()

    def test_delete_folder_closes_the_connection_on_error(self):
        self.server.fail("delete", error=OSError("Connection lost"))

        with self.assertRaises(OSError):
            delete_folder(self.account, self.folder)

        self.assertTrue(MailFolder.objects.filter(uuid=self.folder.uuid).exists())
        self.assert_one_connection_closed()


class RenameFolderTests(IMAPFolderServiceMixin, TestCase):
    """Tests for rename_folder IMAP service."""

    def setUp(self):
        super().setUp()
        self.folder = self._folder("OldName")

    def test_rename_folder_success(self):
        result = rename_folder(self.account, self.folder, "NewName")

        self.assertEqual(
            self.server.named("rename"), [("rename", "OldName", "NewName")]
        )
        self.assertIn("NewName", self.server.mailboxes)
        self.assertNotIn("OldName", self.server.mailboxes)
        self.assert_one_connection_closed()
        self.assertEqual(result.name, "NewName")
        self.assertEqual(result.display_name, "NewName")
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, "NewName")
        self.assertEqual(self.folder.display_name, "NewName")

    def test_rename_folder_hierarchy(self):
        """Renaming to a hierarchical name extracts display_name correctly."""
        result = rename_folder(self.account, self.folder, "Work/Archive")

        self.assertIn("Work/Archive", self.server.mailboxes)
        self.assertEqual(result.name, "Work/Archive")
        self.assertEqual(result.display_name, "Archive")

    def test_a_nested_folder_keeps_its_parent_path(self):
        """The new name is the folder's own: Parent/Old becomes Parent/New,
        not New at the root."""
        folder = self._folder("Parent/Old", "Old")

        rename_folder(self.account, folder, "New")

        self.assertIn("Parent/New", self.server.mailboxes)
        self.assertNotIn("Parent/Old", self.server.mailboxes)
        folder.refresh_from_db()
        self.assertEqual(folder.name, "Parent/New")
        self.assertEqual(folder.display_name, "New")

    def test_a_nested_folder_keeps_its_parent_path_under_another_delimiter(self):
        self.account.imap_delimiter = "."
        folder = self._folder("INBOX.Old", "Old")

        rename_folder(self.account, folder, "New")

        self.assertIn("INBOX.New", self.server.mailboxes)
        folder.refresh_from_db()
        self.assertEqual(folder.name, "INBOX.New")
        self.assertEqual(folder.display_name, "New")

    def test_a_non_ascii_name_goes_out_in_modified_utf7(self):
        """Issue #1759: the server gets the wire form, the label stays as
        typed."""
        rename_folder(self.account, self.folder, "Réunions")

        self.assertEqual(
            self.server.named("rename"), [("rename", "OldName", "R&AOk-unions")]
        )
        self.assertIn("R&AOk-unions", self.server.mailboxes)
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, "R&AOk-unions")
        self.assertEqual(self.folder.display_name, "Réunions")

    def test_an_ampersand_in_a_name_is_escaped(self):
        rename_folder(self.account, self.folder, "R&D")

        self.assertIn("R&-D", self.server.mailboxes)
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, "R&-D")
        self.assertEqual(self.folder.display_name, "R&D")

    def test_the_parent_path_is_not_encoded_a_second_time(self):
        """Only the typed name is encoded: the ``&`` of the parent's wire
        form is not one to escape."""
        folder = self._folder("R&AOk-unions/Old", "Old")

        rename_folder(self.account, folder, "Été")

        self.assertIn("R&AOk-unions/&AMk-t&AOk-", self.server.mailboxes)
        folder.refresh_from_db()
        self.assertEqual(folder.name, "R&AOk-unions/&AMk-t&AOk-")
        self.assertEqual(folder.display_name, "Été")

    def test_subfolders_follow_the_rename(self):
        """The server renames the subtree with the one command. Rows left
        under the old path would be taken for deleted folders by the next
        sync, and their new paths for new ones."""
        work = self._folder("Work")
        self._folder("Work/Projects", "Projects")
        self._folder("Work/Projects/&AMk-t&AOk-", "Été")
        # Shares the prefix without being below it.
        self._folder("Workshop")

        rename_folder(self.account, work, "Travail")

        self.assertEqual(
            self._names(),
            {
                "OldName": "OldName",
                "Travail": "Travail",
                "Travail/Projects": "Projects",
                "Travail/Projects/&AMk-t&AOk-": "Été",
                "Workshop": "Workshop",
            },
        )
        self.assertEqual(set(self.server.mailboxes) - {"INBOX"}, set(self._names()))
        self.assertEqual(len(self.server.named("rename")), 1)

    def test_subfolder_rows_keep_their_messages(self):
        work = self._folder("Work")
        child = self._folder("Work/Projects", "Projects")
        message = MailMessage.objects.create(
            account=self.account, folder=child, imap_uid=7
        )

        rename_folder(self.account, work, "Travail")

        child.refresh_from_db()
        message.refresh_from_db()
        self.assertEqual(child.name, "Travail/Projects")
        self.assertEqual(message.folder, child)

    def test_the_same_name_sends_nothing(self):
        rename_folder(self.account, self.folder, "OldName")

        self.assertEqual(self.server.commands, [])

    def test_rename_folder_imap_failure(self):
        self._folder("OldName/Child", "Child")
        self.server.fail("rename")

        with self.assertRaises(IMAPClientError):
            rename_folder(self.account, self.folder, "FailName")

        # Folder should not have changed locally
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, "OldName")
        self.assertEqual(self.folder.display_name, "OldName")
        self.assertEqual(
            self._names(), {"OldName": "OldName", "OldName/Child": "Child"}
        )
        self.assertIn("OldName", self.server.mailboxes)
        self.assert_one_connection_closed()

    def test_rename_folder_preserves_other_fields(self):
        """Rename should not affect icon, color, or folder_type."""
        self.folder.icon = "star"
        self.folder.color = "text-warning"
        self.folder.save()

        rename_folder(self.account, self.folder, "Renamed")

        self.folder.refresh_from_db()
        self.assertEqual(self.folder.icon, "star")
        self.assertEqual(self.folder.color, "text-warning")
        self.assertEqual(self.folder.folder_type, "other")

    def test_rename_folder_closes_the_connection_on_error(self):
        self.server.fail("rename", error=OSError("Connection lost"))

        with self.assertRaises(OSError):
            rename_folder(self.account, self.folder, "FailName")

        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, "OldName")
        self.assert_one_connection_closed()


class MoveFolderTests(IMAPFolderServiceMixin, TestCase):
    """Tests for move_folder IMAP service."""

    def test_move_folder_uses_wire_encoded_leaf_not_display_name(self):
        """A folder with non-ASCII chars must be renamed using its mUTF-7
        wire-encoded leaf, not the Unicode-decoded display_name. Otherwise the
        IMAP RENAME would push raw Unicode to the server and produce a
        ghost/inaccessible folder."""
        # &AMk-tudes is IMAP Modified UTF-7 for "Études"
        folder = self._folder("Important/&AMk-tudes", "Études")
        self.server.mailbox("Archive")

        move_folder(self.account, folder, "Archive")

        # The new name on the wire must use the original mUTF-7 leaf, not
        # the decoded "Études".
        self.assertEqual(
            self.server.named("rename"),
            [("rename", "Important/&AMk-tudes", "Archive/&AMk-tudes")],
        )
        self.assertIn("Archive/&AMk-tudes", self.server.mailboxes)
        folder.refresh_from_db()
        self.assertEqual(folder.name, "Archive/&AMk-tudes")
        self.assertEqual(folder.display_name, "Études")

    def test_move_folder_to_root_uses_wire_leaf(self):
        """Moving to root (no parent) must also use the wire-encoded leaf."""
        folder = self._folder("Old/&AMk-tudes", "Études")

        move_folder(self.account, folder, "")

        self.assertEqual(
            self.server.named("rename"),
            [("rename", "Old/&AMk-tudes", "&AMk-tudes")],
        )
        folder.refresh_from_db()
        self.assertEqual(folder.name, "&AMk-tudes")
        self.assertEqual(folder.display_name, "Études")

    def test_subfolders_follow_the_move(self):
        folder = self._folder("Old/Work", "Work")
        self._folder("Old/Work/Projects", "Projects")
        self.server.mailbox("Archive")

        move_folder(self.account, folder, "Archive")

        self.assertEqual(
            self._names(),
            {"Archive/Work": "Work", "Archive/Work/Projects": "Projects"},
        )
        self.assertIn("Archive/Work/Projects", self.server.mailboxes)
        self.assertNotIn("Old/Work/Projects", self.server.mailboxes)
