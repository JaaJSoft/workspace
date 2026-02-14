from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.mail.models import MailAccount, MailFolder, MailMessage

User = get_user_model()


class MailTestMixin:
    """Common setup for mail folder tests."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='mailuser', email='mail@test.com', password='pass123',
        )
        self.other_user = User.objects.create_user(
            username='other', email='other@test.com', password='pass123',
        )

        self.account = MailAccount.objects.create(
            owner=self.user,
            email='user@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='user@example.com',
        )
        self.account.set_password('secret')
        self.account.save()

        self.other_account = MailAccount.objects.create(
            owner=self.other_user,
            email='other@example.com',
            imap_host='imap.example.com',
            smtp_host='smtp.example.com',
            username='other@example.com',
        )

        self.inbox = MailFolder.objects.create(
            account=self.account,
            name='INBOX',
            display_name='Inbox',
            folder_type='inbox',
        )
        self.sent = MailFolder.objects.create(
            account=self.account,
            name='Sent',
            display_name='Sent',
            folder_type='sent',
        )
        self.custom = MailFolder.objects.create(
            account=self.account,
            name='MyFolder',
            display_name='MyFolder',
            folder_type='other',
        )
        self.other_folder = MailFolder.objects.create(
            account=self.other_account,
            name='INBOX',
            display_name='Inbox',
            folder_type='inbox',
        )


# ---------- Model Tests ----------


class MailFolderModelTests(MailTestMixin, TestCase):
    """Tests for MailFolder model icon/color fields."""

    def test_icon_color_null_by_default(self):
        self.assertIsNone(self.inbox.icon)
        self.assertIsNone(self.inbox.color)

    def test_set_icon_and_color(self):
        self.inbox.icon = 'star'
        self.inbox.color = 'text-warning'
        self.inbox.save()
        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.icon, 'star')
        self.assertEqual(self.inbox.color, 'text-warning')

    def test_icon_blank_allowed(self):
        self.inbox.icon = ''
        self.inbox.color = ''
        self.inbox.save()
        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.icon, '')
        self.assertEqual(self.inbox.color, '')


# ---------- Folder List & Create API ----------


class MailFolderListTests(MailTestMixin, APITestCase):
    """Tests for GET /api/v1/mail/folders"""

    url = '/api/v1/mail/folders'

    def test_unauthenticated_rejected(self):
        resp = self.client.get(self.url, {'account': self.account.uuid})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_folders(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(self.url, {'account': self.account.uuid})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 3)

    def test_list_folders_includes_icon_color(self):
        self.inbox.icon = 'star'
        self.inbox.color = 'text-info'
        self.inbox.save()

        self.client.force_authenticate(self.user)
        resp = self.client.get(self.url, {'account': self.account.uuid})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        inbox_data = next(f for f in resp.data if f['folder_type'] == 'inbox')
        self.assertEqual(inbox_data['icon'], 'star')
        self.assertEqual(inbox_data['color'], 'text-info')

    def test_list_folders_requires_account_param(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_list_other_user_folders(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(self.url, {'account': self.other_account.uuid})
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class MailFolderCreateTests(MailTestMixin, APITestCase):
    """Tests for POST /api/v1/mail/folders"""

    url = '/api/v1/mail/folders'

    def test_unauthenticated_rejected(self):
        resp = self.client.post(self.url, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @patch('workspace.mail.services.imap.create_folder')
    def test_create_folder(self, mock_create):
        folder = MailFolder.objects.create(
            account=self.account,
            name='NewFolder',
            display_name='NewFolder',
            folder_type='other',
        )
        mock_create.return_value = folder

        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, {
            'account_id': str(self.account.uuid),
            'name': 'NewFolder',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['name'], 'NewFolder')
        self.assertEqual(resp.data['display_name'], 'NewFolder')
        mock_create.assert_called_once_with(self.account, 'NewFolder', parent_name='')

    def test_create_folder_missing_name(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, {
            'account_id': str(self.account.uuid),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_folder_other_user_account(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, {
            'account_id': str(self.other_account.uuid),
            'name': 'Hacked',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('workspace.mail.services.imap.create_folder', side_effect=Exception('IMAP error'))
    def test_create_folder_imap_failure(self, mock_create):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self.url, {
            'account_id': str(self.account.uuid),
            'name': 'BadFolder',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)


# ---------- Folder Update (Icon/Color) ----------


class MailFolderUpdateIconTests(MailTestMixin, APITestCase):
    """Tests for PATCH /api/v1/mail/folders/<uuid> (icon/color)"""

    def _url(self, folder):
        return f'/api/v1/mail/folders/{folder.uuid}'

    def test_unauthenticated_rejected(self):
        resp = self.client.patch(self._url(self.inbox), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_icon(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.inbox), {
            'icon': 'star',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['icon'], 'star')
        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.icon, 'star')

    def test_update_color(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.inbox), {
            'color': 'text-success',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['color'], 'text-success')
        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.color, 'text-success')

    def test_update_icon_and_color(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.inbox), {
            'icon': 'heart',
            'color': 'text-error',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.icon, 'heart')
        self.assertEqual(self.inbox.color, 'text-error')

    def test_clear_icon_with_null(self):
        self.inbox.icon = 'star'
        self.inbox.save()

        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.inbox), {
            'icon': None,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.inbox.refresh_from_db()
        self.assertIsNone(self.inbox.icon)

    def test_cannot_update_other_user_folder(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.other_folder), {
            'icon': 'star',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_nonexistent_folder(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            '/api/v1/mail/folders/00000000-0000-0000-0000-000000000000',
            {'icon': 'star'}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_empty_patch_is_ok(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.inbox), {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


# ---------- Folder Rename ----------


class MailFolderRenameTests(MailTestMixin, APITestCase):
    """Tests for PATCH /api/v1/mail/folders/<uuid> (rename via display_name)"""

    def _url(self, folder):
        return f'/api/v1/mail/folders/{folder.uuid}'

    @patch('workspace.mail.services.imap.rename_folder')
    def test_rename_folder(self, mock_rename):
        def side_effect(account, folder, new_name):
            folder.name = new_name
            folder.display_name = new_name
            folder.save(update_fields=['name', 'display_name', 'updated_at'])
            return folder
        mock_rename.side_effect = side_effect

        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.custom), {
            'display_name': 'RenamedFolder',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['display_name'], 'RenamedFolder')
        mock_rename.assert_called_once()

    def test_rename_same_name_no_imap_call(self):
        """Renaming to the same name should not call IMAP."""
        self.client.force_authenticate(self.user)
        with patch('workspace.mail.services.imap.rename_folder') as mock_rename:
            resp = self.client.patch(self._url(self.custom), {
                'display_name': 'MyFolder',  # same name
            }, format='json')
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            mock_rename.assert_not_called()

    @patch('workspace.mail.services.imap.rename_folder', side_effect=Exception('IMAP error'))
    def test_rename_imap_failure(self, mock_rename):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.custom), {
            'display_name': 'FailRename',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)
        # Folder should not have been renamed locally
        self.custom.refresh_from_db()
        self.assertEqual(self.custom.display_name, 'MyFolder')

    def test_rename_with_icon_combined(self):
        """Icon/color and rename can be sent together."""
        with patch('workspace.mail.services.imap.rename_folder') as mock_rename:
            def side_effect(account, folder, new_name):
                folder.name = new_name
                folder.display_name = new_name
                folder.save(update_fields=['name', 'display_name', 'updated_at'])
                return folder
            mock_rename.side_effect = side_effect

            self.client.force_authenticate(self.user)
            resp = self.client.patch(self._url(self.custom), {
                'display_name': 'NewName',
                'icon': 'rocket',
                'color': 'text-info',
            }, format='json')

            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(resp.data['display_name'], 'NewName')
            self.assertEqual(resp.data['icon'], 'rocket')
            self.assertEqual(resp.data['color'], 'text-info')

    def test_cannot_rename_other_user_folder(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.other_folder), {
            'display_name': 'Hacked',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ---------- Folder Move ----------


class MailFolderMoveTests(MailTestMixin, APITestCase):
    """Tests for PATCH /api/v1/mail/folders/<uuid> (move via parent_name)"""

    def _url(self, folder):
        return f'/api/v1/mail/folders/{folder.uuid}'

    @patch('workspace.mail.services.imap.move_folder')
    def test_move_folder_to_parent(self, mock_move):
        """Move a root folder under another folder."""
        parent = MailFolder.objects.create(
            account=self.account, name='Work', display_name='Work', folder_type='other',
        )

        def side_effect(account, folder, new_parent_name):
            folder.name = f'{new_parent_name}/{folder.display_name}'
            folder.display_name = folder.display_name
            folder.save(update_fields=['name', 'display_name', 'updated_at'])
            return folder
        mock_move.side_effect = side_effect

        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.custom), {
            'parent_name': 'Work',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_move.assert_called_once_with(self.account, self.custom, 'Work')

    @patch('workspace.mail.services.imap.move_folder')
    def test_move_folder_to_root(self, mock_move):
        """Move a subfolder to root using empty parent_name."""
        subfolder = MailFolder.objects.create(
            account=self.account, name='Work/Projects',
            display_name='Projects', folder_type='other',
        )

        def side_effect(account, folder, new_parent_name):
            folder.name = folder.display_name
            folder.save(update_fields=['name', 'display_name', 'updated_at'])
            return folder
        mock_move.side_effect = side_effect

        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(subfolder), {
            'parent_name': '',
        }, format='json')

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_move.assert_called_once_with(self.account, subfolder, '')

    def test_cannot_move_special_folder(self):
        """Special folders (inbox, sent, etc.) cannot be moved."""
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.inbox), {
            'parent_name': 'SomeParent',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('special folder', resp.data['detail'].lower())

    @patch('workspace.mail.services.imap.move_folder', side_effect=Exception('IMAP error'))
    def test_move_imap_failure(self, mock_move):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.custom), {
            'parent_name': 'Destination',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    def test_cannot_move_other_user_folder(self):
        self.client.force_authenticate(self.user)
        resp = self.client.patch(self._url(self.other_folder), {
            'parent_name': 'Hacked',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ---------- Folder Delete ----------


class MailFolderDeleteTests(MailTestMixin, APITestCase):
    """Tests for DELETE /api/v1/mail/folders/<uuid>"""

    def _url(self, folder):
        return f'/api/v1/mail/folders/{folder.uuid}'

    def test_unauthenticated_rejected(self):
        resp = self.client.delete(self._url(self.custom))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    @patch('workspace.mail.services.imap.delete_folder')
    def test_delete_custom_folder(self, mock_delete):
        self.client.force_authenticate(self.user)
        folder_uuid = self.custom.uuid
        resp = self.client.delete(self._url(self.custom))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        mock_delete.assert_called_once()

    def test_cannot_delete_special_folder_inbox(self):
        self.client.force_authenticate(self.user)
        resp = self.client.delete(self._url(self.inbox))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('special folder', resp.data['detail'].lower())

    def test_cannot_delete_special_folder_sent(self):
        self.client.force_authenticate(self.user)
        resp = self.client.delete(self._url(self.sent))
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_cannot_delete_other_user_folder(self):
        self.client.force_authenticate(self.user)
        resp = self.client.delete(self._url(self.other_folder))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    @patch('workspace.mail.services.imap.delete_folder', side_effect=Exception('IMAP error'))
    def test_delete_imap_failure(self, mock_delete):
        self.client.force_authenticate(self.user)
        resp = self.client.delete(self._url(self.custom))
        self.assertEqual(resp.status_code, status.HTTP_502_BAD_GATEWAY)

    def test_delete_nonexistent_folder(self):
        self.client.force_authenticate(self.user)
        resp = self.client.delete(
            '/api/v1/mail/folders/00000000-0000-0000-0000-000000000000',
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


# ---------- Mark All Read ----------


class MailFolderMarkReadTests(MailTestMixin, APITestCase):
    """Tests for POST /api/v1/mail/folders/<uuid>/mark-read"""

    def _url(self, folder):
        return f'/api/v1/mail/folders/{folder.uuid}/mark-read'

    def test_unauthenticated_rejected(self):
        resp = self.client.post(self._url(self.inbox))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_mark_all_read(self):
        # Create unread messages
        for i in range(3):
            MailMessage.objects.create(
                account=self.account,
                folder=self.inbox,
                imap_uid=100 + i,
                is_read=False,
            )
        self.inbox.unread_count = 3
        self.inbox.save()

        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url(self.inbox))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['updated'], 3)

        self.inbox.refresh_from_db()
        self.assertEqual(self.inbox.unread_count, 0)
        self.assertTrue(
            all(m.is_read for m in MailMessage.objects.filter(folder=self.inbox))
        )

    def test_mark_all_read_no_messages(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url(self.inbox))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['updated'], 0)

    def test_cannot_mark_other_user_folder(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url(self.other_folder))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
