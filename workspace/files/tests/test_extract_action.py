from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.actions import ActionRegistry
from workspace.files.models import File
from workspace.files.services import FilePermission

User = get_user_model()


def _make_file(owner, **kwargs):
    defaults = {
        'name': 'archive.zip',
        'node_type': 'file',
        'mime_type': 'application/zip',
        'type': 'zip',
    }
    defaults.update(kwargs)
    f = File(owner=owner, **defaults)
    f.deleted_at = getattr(f, 'deleted_at', None)
    return f


class ExtractActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)

    def _ids(self, file_obj, permission):
        actions = ActionRegistry.get_available_actions(
            self.user, file_obj, permission=permission,
        )
        return {a['id'] for a in actions}

    def test_extract_visible_for_zip_with_edit_permission(self):
        f = _make_file(self.user, mime_type='application/zip')
        self.assertIn('extract', self._ids(f, FilePermission.EDIT))

    def test_extract_visible_for_zip_with_manage_permission(self):
        f = _make_file(self.user, mime_type='application/zip')
        self.assertIn('extract', self._ids(f, FilePermission.MANAGE))

    def test_extract_visible_for_x_zip_compressed_mime(self):
        f = _make_file(self.user, mime_type='application/x-zip-compressed')
        self.assertIn('extract', self._ids(f, FilePermission.EDIT))

    def test_extract_hidden_for_non_zip(self):
        f = _make_file(self.user, mime_type='text/plain', type='txt', name='readme.txt')
        self.assertNotIn('extract', self._ids(f, FilePermission.EDIT))

    def test_extract_hidden_for_tar(self):
        f = _make_file(self.user, mime_type='application/x-tar', type='tar', name='archive.tar')
        self.assertNotIn('extract', self._ids(f, FilePermission.EDIT))

    def test_extract_hidden_without_edit_permission(self):
        f = _make_file(self.user, mime_type='application/zip')
        self.assertNotIn('extract', self._ids(f, FilePermission.VIEW))
        self.assertNotIn('extract', self._ids(f, FilePermission.WRITE))

    def test_extract_hidden_when_trashed(self):
        f = _make_file(self.user, mime_type='application/zip')
        f.deleted_at = timezone.now()
        self.assertNotIn('extract', self._ids(f, FilePermission.EDIT))

    def test_extract_hidden_on_folder(self):
        folder = _make_file(self.user, node_type='folder', mime_type=None, type=None, name='things')
        self.assertNotIn('extract', self._ids(folder, FilePermission.EDIT))
