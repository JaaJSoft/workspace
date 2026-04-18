from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File
from workspace.notes.services.journal import is_journal_note
from workspace.users.services.settings import set_setting

User = get_user_model()


class IsJournalNoteTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )
        self.notes_folder = File.objects.create(
            owner=self.user, name='Notes', node_type=File.NodeType.FOLDER,
        )
        self.journal_folder = File.objects.create(
            owner=self.user, name='Journal', node_type=File.NodeType.FOLDER,
            parent=self.notes_folder,
        )
        self.other_folder = File.objects.create(
            owner=self.user, name='Other', node_type=File.NodeType.FOLDER,
        )
        set_setting(self.user, 'notes', 'preferences', {
            'journalFolderUuid': str(self.journal_folder.uuid),
        })

    def _make_note(self, parent, name='2026-04-17.md'):
        return File.objects.create(
            owner=self.user, name=name, node_type=File.NodeType.FILE,
            mime_type='text/markdown', parent=parent,
        )

    def test_direct_child_returns_true(self):
        note = self._make_note(self.journal_folder)
        self.assertTrue(is_journal_note(self.user, note))

    def test_different_parent_returns_false(self):
        note = self._make_note(self.other_folder)
        self.assertFalse(is_journal_note(self.user, note))

    def test_root_note_returns_false(self):
        note = self._make_note(parent=None, name='root.md')
        self.assertFalse(is_journal_note(self.user, note))

    def test_folder_returns_false(self):
        subfolder = File.objects.create(
            owner=self.user, name='Sub', node_type=File.NodeType.FOLDER,
            parent=self.journal_folder,
        )
        self.assertFalse(is_journal_note(self.user, subfolder))

    def test_soft_deleted_returns_false(self):
        note = self._make_note(self.journal_folder)
        note.deleted_at = timezone.now()
        note.save(update_fields=['deleted_at'])
        self.assertFalse(is_journal_note(self.user, note))

    def test_empty_prefs_returns_false(self):
        other = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )
        note = self._make_note(self.journal_folder)
        # bob has no prefs -> journalFolderUuid missing -> must return False
        self.assertFalse(is_journal_note(other, note))

    def test_indirect_descendant_returns_false(self):
        sub = File.objects.create(
            owner=self.user, name='Sub', node_type=File.NodeType.FOLDER,
            parent=self.journal_folder,
        )
        grandchild = self._make_note(sub, name='nested.md')
        self.assertFalse(is_journal_note(self.user, grandchild))

    def test_none_file_returns_false(self):
        self.assertFalse(is_journal_note(self.user, None))
