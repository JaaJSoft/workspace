"""Tests for the Notes/Journal default-folder bootstrap."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.services import tree
from workspace.notes.ui.views import _ensure_default_folders
from workspace.users.services.settings import get_setting

User = get_user_model()


class EnsureDefaultFoldersTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="journalroot",
            email="jroot@test.com",
            password="pass",
        )

    def tearDown(self):
        cache.clear()

    def test_a_root_folder_named_journal_stays_where_the_user_put_it(self):
        root_journal = tree.create_folder(self.user, "Journal")
        note = tree.create_file(
            self.user,
            "plan.md",
            parent=root_journal,
            content=ContentFile(b"mine", name="plan.md"),
        )

        _ensure_default_folders(self.user)

        root_journal.refresh_from_db()
        note.refresh_from_db()
        self.assertIsNone(root_journal.parent_id)
        self.assertEqual(note.parent_id, root_journal.uuid)
        journal_uuid = get_setting(self.user, "notes", "preferences")[
            "journalFolderUuid"
        ]
        self.assertNotEqual(journal_uuid, str(root_journal.uuid))
        journal = root_journal.__class__.objects.get(uuid=journal_uuid)
        self.assertEqual(journal.name, "Journal")
        self.assertEqual(journal.parent.name, "Notes")

    def test_an_existing_notes_journal_is_adopted(self):
        notes = tree.create_folder(self.user, "Notes")
        inner_journal = tree.create_folder(self.user, "Journal", parent=notes)

        _ensure_default_folders(self.user)

        self.assertEqual(
            get_setting(self.user, "notes", "preferences")["journalFolderUuid"],
            str(inner_journal.uuid),
        )


class EnsureDefaultFoldersMalformedPrefsTests(TestCase):
    """User-controlled prefs may contain non-UUID strings; the bootstrap
    must not crash with ValidationError -> 500."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="badprefs",
            email="bp@test.com",
            password="pass",
        )

    def tearDown(self):
        cache.clear()

    def test_malformed_default_folder_uuid_does_not_crash(self):
        """Regression: a non-UUID ``defaultFolderUuid`` (set via the
        user-settings API) used to crash _ensure_default_folders because
        UUIDField.to_python raised ValidationError on filter(uuid=...)."""
        from workspace.users.services.settings import set_setting

        set_setting(
            self.user,
            "notes",
            "preferences",
            {
                "defaultFolderUuid": "not-a-uuid",
                "journalFolderUuid": "also-bad",
            },
        )

        # Did not raise: malformed UUIDs fall back to "no preference",
        # so the bootstrap creates fresh folders as if no prefs existed.
        prefs, changed = _ensure_default_folders(self.user)
        self.assertTrue(changed)
        # The bootstrap rewrote prefs with valid UUIDs.
        from uuid import UUID

        UUID(prefs["defaultFolderUuid"])
        UUID(prefs["journalFolderUuid"])
