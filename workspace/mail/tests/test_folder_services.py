from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage
from workspace.mail.services.imap import _quote_mailbox, create_folder, delete_folder, rename_folder

User = get_user_model()


class IMAPFolderServiceMixin:
    """Common setup for IMAP folder service tests."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='imapuser', email='imap@test.com', password='pass123',
        )
        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@example.com',
            imap_host='imap.example.com',
            imap_use_ssl=True,
            smtp_host='smtp.example.com',
            username='user@example.com',
        )
        self.account.set_password('secret')
        self.account.save()

    def _mock_conn(self):
        conn = MagicMock()
        conn.create.return_value = ('OK', [b'Success'])
        conn.delete.return_value = ('OK', [b'Success'])
        conn.rename.return_value = ('OK', [b'Success'])
        return conn


class CreateFolderTests(IMAPFolderServiceMixin, TestCase):
    """Tests for create_folder IMAP service."""

    @patch('workspace.mail.services.imap.connect_imap')
    def test_create_folder_success(self, mock_connect):
        mock_conn = self._mock_conn()
        mock_connect.return_value = mock_conn

        folder = create_folder(self.account, 'Projects')

        mock_conn.create.assert_called_once_with(_quote_mailbox('Projects'))
        mock_conn.logout.assert_called_once()
        self.assertIsNotNone(folder)
        self.assertEqual(folder.name, 'Projects')
        self.assertEqual(folder.display_name, 'Projects')
        self.assertEqual(folder.folder_type, 'other')
        self.assertEqual(folder.account, self.account)
        self.assertTrue(MailFolder.objects.filter(account=self.account, name='Projects').exists())

    @patch('workspace.mail.services.imap.connect_imap')
    def test_create_folder_with_hierarchy(self, mock_connect):
        mock_conn = self._mock_conn()
        mock_connect.return_value = mock_conn

        folder = create_folder(self.account, 'Work/Projects')

        mock_conn.create.assert_called_once_with(_quote_mailbox('Work/Projects'))
        self.assertEqual(folder.name, 'Work/Projects')
        self.assertEqual(folder.display_name, 'Projects')  # last segment

    @patch('workspace.mail.services.imap.connect_imap')
    def test_create_folder_imap_failure(self, mock_connect):
        mock_conn = self._mock_conn()
        mock_conn.create.return_value = ('NO', [b'Folder already exists'])
        mock_connect.return_value = mock_conn

        with self.assertRaises(Exception):
            create_folder(self.account, 'Existing')

        # Should not have created local folder
        self.assertFalse(
            MailFolder.objects.filter(account=self.account, name='Existing').exists()
        )
        mock_conn.logout.assert_called_once()

    @patch('workspace.mail.services.imap.connect_imap')
    def test_create_folder_logout_on_error(self, mock_connect):
        """Logout should be called even if create raises."""
        mock_conn = self._mock_conn()
        mock_conn.create.side_effect = Exception('Connection lost')
        mock_connect.return_value = mock_conn

        with self.assertRaises(Exception):
            create_folder(self.account, 'BadFolder')

        mock_conn.logout.assert_called_once()


class DeleteFolderTests(IMAPFolderServiceMixin, TestCase):
    """Tests for delete_folder IMAP service."""

    def setUp(self):
        super().setUp()
        self.folder = MailFolder.objects.create(
            account=self.account,
            name='ToDelete',
            display_name='ToDelete',
            folder_type='other',
        )
        # Add some messages to this folder
        for i in range(3):
            MailMessage.objects.create(
                account=self.account,
                folder=self.folder,
                imap_uid=200 + i,
            )

    @patch('workspace.mail.services.imap.connect_imap')
    def test_delete_folder_success(self, mock_connect):
        mock_conn = self._mock_conn()
        mock_connect.return_value = mock_conn
        folder_uuid = self.folder.uuid

        delete_folder(self.account, self.folder)

        mock_conn.delete.assert_called_once_with(_quote_mailbox('ToDelete'))
        mock_conn.logout.assert_called_once()
        self.assertFalse(MailFolder.objects.filter(uuid=folder_uuid).exists())
        # Messages should also be deleted (cascade)
        self.assertFalse(MailMessage.objects.filter(folder_id=folder_uuid).exists())

    @patch('workspace.mail.services.imap.connect_imap')
    def test_delete_folder_imap_failure(self, mock_connect):
        mock_conn = self._mock_conn()
        mock_conn.delete.return_value = ('NO', [b'Cannot delete'])
        mock_connect.return_value = mock_conn

        with self.assertRaises(Exception):
            delete_folder(self.account, self.folder)

        # Folder should still exist locally
        self.assertTrue(MailFolder.objects.filter(uuid=self.folder.uuid).exists())
        mock_conn.logout.assert_called_once()

    @patch('workspace.mail.services.imap.connect_imap')
    def test_delete_folder_logout_on_error(self, mock_connect):
        mock_conn = self._mock_conn()
        mock_conn.delete.side_effect = Exception('Connection lost')
        mock_connect.return_value = mock_conn

        with self.assertRaises(Exception):
            delete_folder(self.account, self.folder)

        mock_conn.logout.assert_called_once()


class RenameFolderTests(IMAPFolderServiceMixin, TestCase):
    """Tests for rename_folder IMAP service."""

    def setUp(self):
        super().setUp()
        self.folder = MailFolder.objects.create(
            account=self.account,
            name='OldName',
            display_name='OldName',
            folder_type='other',
        )

    @patch('workspace.mail.services.imap.connect_imap')
    def test_rename_folder_success(self, mock_connect):
        mock_conn = self._mock_conn()
        mock_connect.return_value = mock_conn

        result = rename_folder(self.account, self.folder, 'NewName')

        mock_conn.rename.assert_called_once_with(_quote_mailbox('OldName'), _quote_mailbox('NewName'))
        mock_conn.logout.assert_called_once()
        self.assertEqual(result.name, 'NewName')
        self.assertEqual(result.display_name, 'NewName')
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, 'NewName')
        self.assertEqual(self.folder.display_name, 'NewName')

    @patch('workspace.mail.services.imap.connect_imap')
    def test_rename_folder_hierarchy(self, mock_connect):
        """Renaming to a hierarchical name extracts display_name correctly."""
        mock_conn = self._mock_conn()
        mock_connect.return_value = mock_conn

        result = rename_folder(self.account, self.folder, 'Work/Archive')

        self.assertEqual(result.name, 'Work/Archive')
        self.assertEqual(result.display_name, 'Archive')

    @patch('workspace.mail.services.imap.connect_imap')
    def test_rename_folder_imap_failure(self, mock_connect):
        mock_conn = self._mock_conn()
        mock_conn.rename.return_value = ('NO', [b'Cannot rename'])
        mock_connect.return_value = mock_conn

        with self.assertRaises(Exception):
            rename_folder(self.account, self.folder, 'FailName')

        # Folder should not have changed locally
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.name, 'OldName')
        self.assertEqual(self.folder.display_name, 'OldName')
        mock_conn.logout.assert_called_once()

    @patch('workspace.mail.services.imap.connect_imap')
    def test_rename_folder_preserves_other_fields(self, mock_connect):
        """Rename should not affect icon, color, or folder_type."""
        self.folder.icon = 'star'
        self.folder.color = 'text-warning'
        self.folder.save()

        mock_conn = self._mock_conn()
        mock_connect.return_value = mock_conn

        rename_folder(self.account, self.folder, 'Renamed')

        self.folder.refresh_from_db()
        self.assertEqual(self.folder.icon, 'star')
        self.assertEqual(self.folder.color, 'text-warning')
        self.assertEqual(self.folder.folder_type, 'other')

    @patch('workspace.mail.services.imap.connect_imap')
    def test_rename_folder_logout_on_error(self, mock_connect):
        mock_conn = self._mock_conn()
        mock_conn.rename.side_effect = Exception('Connection lost')
        mock_connect.return_value = mock_conn

        with self.assertRaises(Exception):
            rename_folder(self.account, self.folder, 'FailName')

        mock_conn.logout.assert_called_once()
