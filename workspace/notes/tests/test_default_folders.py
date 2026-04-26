"""Tests for the Notes/Journal default-folder bootstrap and migration logic.

`_ensure_default_folders` runs on every notes UI page load. On a legacy
account (root-level "Journal" folder), it re-parents Journal under Notes.
This test class verifies that re-parenting goes through the FileService so
descendant content storage paths follow the move on disk.
"""

import os
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.notes.ui.views import _ensure_default_folders

User = get_user_model()


class EnsureDefaultFoldersStorageTests(TestCase):
    """Verifies the legacy Journal migration migrates content on disk."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._media_override = override_settings(MEDIA_ROOT=self._tmpdir)
        self._media_override.enable()

        self.user = User.objects.create_user(
            username='journalmig', email='jmig@test.com', password='pass',
        )

    def tearDown(self):
        cache.clear()
        self._media_override.disable()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    @override_settings(DEFAULT_FILE_STORAGE='django.core.files.storage.FileSystemStorage')
    def test_legacy_journal_migration_moves_descendant_content(self):
        """A root-level Journal containing a note must migrate the note's bytes
        when re-parented under Notes.

        Regression: the migration used a raw `parent = ...; save()` which
        bypassed FileService.move() and left the note's bytes orphaned at
        the root-level path while the DB row pointed under Notes/Journal.
        """
        journal = FileService.create_folder(
            self.user, 'Journal', icon='book-open', color='success',
        )
        note = FileService.create_file(
            self.user, '2026-04-26.md', parent=journal,
            content=ContentFile(b'today I refactored', name='2026-04-26.md'),
        )
        old_full_path = os.path.join(self._tmpdir, note.content.name)
        self.assertTrue(os.path.isfile(old_full_path))

        _ensure_default_folders(self.user)

        note.refresh_from_db()
        new_content_name = note.content.name.replace('\\', '/')
        self.assertTrue(
            new_content_name.startswith('files/users/journalmig/Notes/Journal/'),
            f'Expected new path under Notes/Journal/, got {new_content_name}',
        )
        new_full_path = os.path.join(self._tmpdir, note.content.name)
        self.assertTrue(os.path.isfile(new_full_path))
        self.assertFalse(os.path.isfile(old_full_path))

        # The Journal folder itself must now live under Notes.
        journal.refresh_from_db()
        self.assertIsNotNone(journal.parent_id)
        self.assertEqual(journal.parent.name, 'Notes')
