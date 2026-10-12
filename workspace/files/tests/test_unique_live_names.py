"""One live node per name and folder, held by the database itself."""

import importlib

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from workspace.common.tests.migrations import schema_editor_stub
from workspace.files.models import File
from workspace.files.services import FileService

migration = importlib.import_module(
    "workspace.files.migrations.0063_resolve_duplicate_names"
)

User = get_user_model()


def read(file_obj):
    file_obj.refresh_from_db()
    with file_obj.content.open("rb") as fh:
        return fh.read()


class LiveNameConstraintTests(TestCase):
    """Rows written around the service, so only the constraints stand in the way."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.other = User.objects.create_user(username="bob", password="pass")

    def _row(self, name, owner=None, **fields):
        return File.objects.create(
            owner=owner or self.user, name=name, node_type="file", **fields
        )

    def assertRefused(self, name, **fields):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._row(name, **fields)

    def test_a_case_variant_is_refused_at_the_root(self):
        self._row("Report.pdf")
        self.assertRefused("report.PDF")

    def test_a_file_and_a_folder_share_one_namespace(self):
        File.objects.create(owner=self.user, name="Docs", node_type="folder")
        self.assertRefused("docs")

    def test_a_case_variant_is_refused_in_a_personal_folder(self):
        folder = File.objects.create(owner=self.user, name="Docs", node_type="folder")
        self._row("a.txt", parent=folder)
        self.assertRefused("A.txt", parent=folder)

    def test_group_members_share_the_group_folder_namespace(self):
        group = Group.objects.create(name="Team")
        root = File.objects.create(
            owner=self.user, name="Team", node_type="folder", group=group
        )
        self._row("a.txt", parent=root, group=group)
        self.assertRefused("A.TXT", owner=self.other, parent=root, group=group)

    def test_a_trashed_node_frees_its_name(self):
        from django.utils import timezone

        self._row("a.txt", deleted_at=timezone.now())
        self._row("a.txt")
        self._row("a.txt", deleted_at=timezone.now())

    def test_two_owners_each_have_a_root(self):
        self._row("a.txt")
        self._row("a.txt", owner=self.other)

    def test_the_same_name_lives_in_two_folders(self):
        first = File.objects.create(owner=self.user, name="One", node_type="folder")
        second = File.objects.create(owner=self.user, name="Two", node_type="folder")
        self._row("a.txt", parent=first)
        self._row("a.txt", parent=second)

    def test_a_group_root_does_not_compete_with_personal_roots(self):
        group = Group.objects.create(name="Team")
        File.objects.create(
            owner=self.user, name="Team", node_type="folder", group=group
        )
        self._row("Team")


class RestoreFolderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.folder = FileService.create_folder(self.user, "Docs")

    def test_restoring_a_folder_leaves_a_child_trashed_on_its_own_in_the_trash(self):
        old = FileService.create_file(
            self.user, "a.txt", parent=self.folder, content=ContentFile(b"old")
        )
        FileService.soft_delete(old, acting_user=self.user)
        new = FileService.create_file(
            self.user, "a.txt", parent=self.folder, content=ContentFile(b"new")
        )
        FileService.soft_delete(self.folder, acting_user=self.user)

        self.folder.refresh_from_db()
        FileService.restore(self.folder, acting_user=self.user)

        new.refresh_from_db()
        old.refresh_from_db()
        self.assertIsNone(new.deleted_at)
        self.assertIsNotNone(old.deleted_at, "it was deleted before the folder")
        with new.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"new")
        with old.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"old")

        # Back in the trash list on its own, and restorable from there.
        FileService.soft_delete(new, acting_user=self.user)
        FileService.restore(old, acting_user=self.user)
        old.refresh_from_db()
        self.assertIsNone(old.deleted_at)
        with old.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"old")

    def test_a_folder_trashed_on_its_own_stays_out_of_its_replacement(self):
        root = FileService.create_folder(self.user, "R")
        old = FileService.create_folder(self.user, "C", parent=root)
        FileService.create_file(
            self.user, "c1.txt", parent=old, content=ContentFile(b"1")
        )
        FileService.soft_delete(old, acting_user=self.user)
        new = FileService.create_folder(self.user, "C", parent=root)
        c2 = FileService.create_file(
            self.user, "c2.txt", parent=new, content=ContentFile(b"2")
        )
        FileService.soft_delete(root, acting_user=self.user)

        root.refresh_from_db()
        FileService.restore(root, acting_user=self.user)

        old.refresh_from_db()
        self.assertIsNotNone(old.deleted_at)
        self.assertEqual(read(c2), b"2")

        FileService.soft_delete(new, acting_user=self.user)
        old.refresh_from_db()
        FileService.restore(old, acting_user=self.user)
        c1 = File.objects.get(name="c1.txt")
        self.assertIsNone(c1.deleted_at)
        self.assertEqual(read(c1), b"1")

    def test_a_name_taken_during_the_restore_falls_back_to_a_copy(self):
        from unittest.mock import patch

        from workspace.files.services import _names

        trashed = FileService.create_file(
            self.user, "a.txt", parent=self.folder, content=ContentFile(b"old")
        )
        FileService.soft_delete(trashed, acting_user=self.user)
        find_node_conflict = _names.find_node_conflict
        raced = []

        def racing_find(*args, **kwargs):
            conflict = find_node_conflict(*args, **kwargs)
            if not raced:
                raced.append(None)
                raced[0] = FileService.create_file(
                    self.user, "a.txt", parent=self.folder, content=ContentFile(b"new")
                )
            return conflict

        trashed.refresh_from_db()
        with patch.object(_names, "find_node_conflict", racing_find):
            FileService.restore(trashed, acting_user=self.user)

        trashed.refresh_from_db()
        self.assertEqual(trashed.name, "a (Copy).txt")
        self.assertIsNone(trashed.deleted_at)
        self.assertEqual(read(trashed), b"old")
        self.assertEqual(read(raced[0]), b"new")


class ResolveDuplicateNamesMigrationTests(TestCase):
    """The duplicates an install may hold from before the constraints."""

    def setUp(self):
        # Rolled back with the test: DDL is transactional on both backends.
        with connection.cursor() as cursor:
            for name in File.LIVE_NAME_CONSTRAINTS:
                cursor.execute(f'DROP INDEX "{name}"')
        self.user = User.objects.create_user(username="alice", password="pass")

    def _migrate(self):
        migration.resolve_duplicate_names(apps, schema_editor_stub())

    def _row(self, name, data=None, **fields):
        if data is not None:
            fields["content"] = ContentFile(data, name=name)
        fields.setdefault("node_type", "file")
        return File.objects.create(owner=self.user, name=name, **fields)

    def _read(self, row):
        row.refresh_from_db()
        with row.content.open("rb") as fh:
            return fh.read()

    def test_the_oldest_keeps_its_name_and_a_case_variant_becomes_a_copy(self):
        keeper = self._row("report.txt", b"one")
        variant = self._row("Report.TXT", b"two")

        self._migrate()

        keeper.refresh_from_db()
        variant.refresh_from_db()
        self.assertEqual(keeper.name, "report.txt")
        self.assertEqual(variant.name, "Report (Copy).TXT")
        self.assertEqual(variant.path, "Report (Copy).TXT")
        self.assertEqual(variant.content.name, "files/users/alice/Report (Copy).TXT")
        self.assertTrue(variant.content.storage.exists(variant.content.name))
        self.assertTrue(keeper.content.storage.exists(keeper.content.name))

    def test_an_exact_duplicate_gets_its_own_copy_of_the_shared_blob(self):
        keeper = self._row("a.txt", b"bytes")
        twin = self._row("a.txt", content=keeper.content.name)

        self._migrate()

        twin.refresh_from_db()
        self.assertEqual(twin.name, "a (Copy).txt")
        self.assertNotEqual(twin.content.name, keeper.content.name)
        self.assertEqual(self._read(keeper), b"bytes")
        self.assertEqual(self._read(twin), b"bytes")

    def test_a_duplicate_folder_takes_its_own_subtree_along(self):
        keeper = self._row("Docs", node_type="folder")
        kept_child = self._row("x.txt", b"kept", parent=keeper)
        twin = self._row("Docs", node_type="folder")
        sub = self._row("Sub", node_type="folder", parent=twin)
        moved_child = self._row("y.txt", b"mine", parent=sub)

        self._migrate()

        twin.refresh_from_db()
        sub.refresh_from_db()
        moved_child.refresh_from_db()
        self.assertEqual(twin.name, "Docs (Copy)")
        self.assertEqual(sub.path, "Docs (Copy)/Sub")
        self.assertEqual(moved_child.path, "Docs (Copy)/Sub/y.txt")
        self.assertEqual(
            moved_child.content.name, "files/users/alice/Docs (Copy)/Sub/y.txt"
        )
        self.assertEqual(self._read(moved_child), b"mine")
        self.assertEqual(self._read(kept_child), b"kept")
        self.assertEqual(kept_child.path, "Docs/x.txt")

    def test_a_file_row_pointing_at_a_directory_leaves_the_directory_alone(self):
        folder = self._row("Docs", node_type="folder")
        inside = self._row("x.txt", b"inside", parent=folder)
        stale = self._row("docs", content="files/users/alice/Docs")

        self._migrate()

        stale.refresh_from_db()
        self.assertEqual(stale.name, "docs (Copy)")
        self.assertEqual(stale.content.name, "files/users/alice/Docs")
        self.assertEqual(self._read(inside), b"inside")

    def test_group_members_duplicates_are_resolved_too(self):
        group = Group.objects.create(name="Team")
        root = self._row("Team", node_type="folder", group=group)
        teammate = User.objects.create_user(username="bob", password="pass")
        self._row("a.txt", parent=root, group=group)
        File.objects.create(
            owner=teammate, name="A.txt", node_type="file", parent=root, group=group
        )

        self._migrate()

        self.assertEqual(
            sorted(File.objects.filter(parent=root).values_list("name", flat=True)),
            ["A (Copy).txt", "a.txt"],
        )

    def test_trashed_rows_and_other_folders_are_left_alone(self):
        from django.utils import timezone

        self._row("a.txt", deleted_at=timezone.now())
        self._row("a.txt")
        folder = self._row("Docs", node_type="folder")
        self._row("a.txt", parent=folder)

        self._migrate()

        self.assertEqual(
            list(File.objects.filter(node_type="file").values_list("name", flat=True)),
            ["a.txt", "a.txt", "a.txt"],
        )
