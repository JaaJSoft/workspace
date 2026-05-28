from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.actions import ActionRegistry
from workspace.files.services import FilePermission

from .test_actions import _make_file, _make_folder

MANAGE = FilePermission.MANAGE
VIEW = FilePermission.VIEW

User = get_user_model()


class ViewActionTests(TestCase):
    """Tests for ViewAction.is_available."""

    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_viewable_file(self):
        f = _make_file(self.user, mime_type='text/plain')
        action = ActionRegistry.get('view')
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_shared_viewable_file(self):
        f = _make_file(self.user, mime_type='text/plain')
        action = ActionRegistry.get('view')
        self.assertTrue(action.is_available(self.other, f, permission=VIEW))

    def test_not_viewable_file(self):
        f = _make_file(
            self.user,
            name='mystery.bin',
            mime_type='application/octet-stream',
            type='unknown',
        )
        action = ActionRegistry.get('view')
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))

    def test_deleted_file(self):
        f = _make_file(self.user, mime_type='text/plain')
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('view')
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))

    def test_folder_not_applicable(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('view')
        # is_available should refuse folders since they aren't viewable.
        self.assertFalse(action.is_available(self.user, folder, permission=MANAGE))


class OpenFolderActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_folder_available(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('open')
        self.assertTrue(action.is_available(self.user, folder, permission=MANAGE))

    def test_deleted_folder(self):
        folder = _make_folder(self.user)
        folder.deleted_at = timezone.now()
        action = ActionRegistry.get('open')
        self.assertFalse(action.is_available(self.user, folder, permission=MANAGE))

    def test_not_owner(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('open')
        self.assertFalse(action.is_available(self.other, folder, permission=None))


class OpenNewTabActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('open_new_tab')
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_shared_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('open_new_tab')
        self.assertTrue(action.is_available(self.other, f, permission=VIEW))

    def test_no_access(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('open_new_tab')
        self.assertFalse(action.is_available(self.other, f, permission=None))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('open_new_tab')
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))


class DownloadActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('download')
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('download')
        self.assertTrue(action.is_available(self.user, folder, permission=MANAGE))

    def test_shared_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('download')
        self.assertTrue(action.is_available(self.other, f, permission=VIEW))

    def test_no_access(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('download')
        self.assertFalse(action.is_available(self.other, f, permission=None))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('download')
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))

    def test_folder_label(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get('download')
        self.assertEqual(action.get_label(folder), 'Download as ZIP')

    def test_file_label(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('download')
        self.assertEqual(action.get_label(f), 'Download')


class CopyLinkActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('copy_link')
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('copy_link')
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))


class PropertiesActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('properties')
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_shared(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('properties')
        self.assertTrue(action.is_available(self.other, f, permission=VIEW))

    def test_no_access(self):
        f = _make_file(self.user)
        action = ActionRegistry.get('properties')
        self.assertFalse(action.is_available(self.other, f, permission=None))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get('properties')
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))
