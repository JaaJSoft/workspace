from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.files.actions import ActionRegistry
from workspace.files.models import File
from workspace.files.services import FilePermission
from workspace.users.services.settings import set_setting

from .test_actions import _make_file, _make_folder

MANAGE = FilePermission.MANAGE
WRITE = FilePermission.WRITE

User = get_user_model()


class RenameActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def tearDown(self):
        # set_setting populates LocMemCache (process-global, not auto-reset
        # between TestCase runs). Clear to keep test order independent.
        cache.clear()

    def test_owner_file(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("rename")
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get("rename")
        self.assertTrue(action.is_available(self.user, folder, permission=MANAGE))

    def test_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("rename")
        self.assertFalse(action.is_available(self.other, f, permission=WRITE))

    def test_not_available_for_journal_note(self):
        user = User.objects.create_user(
            username="journal_user",
            email="j@test.com",
            password="x",
        )
        journal = File.objects.create(
            owner=user,
            name="Journal",
            node_type=File.NodeType.FOLDER,
        )
        set_setting(
            user,
            "notes",
            "preferences",
            {
                "journalFolderUuid": str(journal.uuid),
            },
        )
        note = File.objects.create(
            owner=user,
            name="2026-04-17.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            parent=journal,
        )
        note.is_favorite = False
        note.is_pinned = False
        note.is_shared = False
        note.has_children = False
        note.deleted_at = None

        action = ActionRegistry.get("rename")
        self.assertFalse(action.is_available(user, note, permission=MANAGE))

    def test_available_for_journal_subfolder_descendant(self):
        user = User.objects.create_user(
            username="subfolder_user",
            email="s@test.com",
            password="x",
        )
        journal = File.objects.create(
            owner=user,
            name="Journal",
            node_type=File.NodeType.FOLDER,
        )
        sub = File.objects.create(
            owner=user,
            name="Sub",
            node_type=File.NodeType.FOLDER,
            parent=journal,
        )
        set_setting(
            user,
            "notes",
            "preferences",
            {
                "journalFolderUuid": str(journal.uuid),
            },
        )
        note = File.objects.create(
            owner=user,
            name="note.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            parent=sub,
        )
        note.is_favorite = False
        note.is_pinned = False
        note.is_shared = False
        note.has_children = False
        note.deleted_at = None

        action = ActionRegistry.get("rename")
        self.assertTrue(action.is_available(user, note, permission=MANAGE))

    def test_available_for_non_journal_note(self):
        user = User.objects.create_user(
            username="normal_user",
            email="n@test.com",
            password="x",
        )
        journal = File.objects.create(
            owner=user,
            name="Journal",
            node_type=File.NodeType.FOLDER,
        )
        other = File.objects.create(
            owner=user,
            name="Other",
            node_type=File.NodeType.FOLDER,
        )
        set_setting(
            user,
            "notes",
            "preferences",
            {
                "journalFolderUuid": str(journal.uuid),
            },
        )
        note = File.objects.create(
            owner=user,
            name="note.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            parent=other,
        )
        note.is_favorite = False
        note.is_pinned = False
        note.is_shared = False
        note.has_children = False
        note.deleted_at = None

        action = ActionRegistry.get("rename")
        self.assertTrue(action.is_available(user, note, permission=MANAGE))


class CutCopyPasteActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_cut_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("cut")
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_cut_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("cut")
        self.assertFalse(action.is_available(self.other, f, permission=None))

    def test_copy_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("copy")
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_copy_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("copy")
        self.assertFalse(action.is_available(self.other, f, permission=None))

    def test_paste_into_owner_folder(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get("paste_into")
        self.assertTrue(action.is_available(self.user, folder, permission=MANAGE))

    def test_paste_into_not_owner(self):
        folder = _make_folder(self.user)
        action = ActionRegistry.get("paste_into")
        self.assertFalse(action.is_available(self.other, folder, permission=None))


class DeleteActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("delete")
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_not_owner(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("delete")
        self.assertFalse(action.is_available(self.other, f, permission=None))

    def test_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get("delete")
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))

    def test_css_class(self):
        action = ActionRegistry.get("delete")
        self.assertEqual(action.css_class, "text-error")


class RestoreActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get("restore")
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_not_deleted(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("restore")
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))

    def test_not_owner(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get("restore")
        self.assertFalse(action.is_available(self.other, f, permission=None))


class PurgeActionTests(TestCase):
    def setUp(self):
        self.user = User(pk=1)
        self.other = User(pk=2)

    def test_owner_deleted(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get("purge")
        self.assertTrue(action.is_available(self.user, f, permission=MANAGE))

    def test_not_deleted(self):
        f = _make_file(self.user)
        action = ActionRegistry.get("purge")
        self.assertFalse(action.is_available(self.user, f, permission=MANAGE))

    def test_not_owner(self):
        f = _make_file(self.user)
        f.deleted_at = timezone.now()
        action = ActionRegistry.get("purge")
        self.assertFalse(action.is_available(self.other, f, permission=None))

    def test_css_class(self):
        action = ActionRegistry.get("purge")
        self.assertEqual(action.css_class, "text-error")
