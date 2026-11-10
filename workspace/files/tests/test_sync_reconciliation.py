"""Behavioral contract for the disk <-> DB reconciliation walk.

``test_sync.py`` pins the storage prefix; this module pins *what the walk
decides* - which nodes get created, which get soft-deleted, which are left
alone - plus the query budget it spends doing so.

The query-count test is the load-bearing one: the walk is driven by celery
beat across every active user's whole tree, so a regression from a constant
number of queries per user back to a per-folder number is invisible in
correctness terms and very visible in production.
"""

import unicodedata
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from workspace.common.tests.media import IsolatedMediaRootMixin, local_storage_only
from workspace.files.models import File, FileEvent
from workspace.files.services import trash, tree
from workspace.files.services.shortcuts import create_shortcut
from workspace.files.sync import FileSyncService

User = get_user_model()


class SyncReconciliationTestCase(IsolatedMediaRootMixin, TestCase):
    """Shared disk-tree scaffolding rooted at the canonical storage prefix.

    Every test asserts on how many nodes the walk created, so it needs a tree
    holding nothing but what it put there itself - hence the isolated root.
    """

    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="reconciler", email="r@test.com", password="pass"
        )

    def _root(self):
        return f"files/users/{self.user.username}"

    def _write(self, *parts, contents=b"data"):
        self._keep_root()
        name = "/".join([self._root(), *parts])
        default_storage.replace(name, ContentFile(contents))
        return name

    def _mkdir(self, *parts):
        self._keep_root()
        name = "/".join([self._root(), *parts])
        default_storage.make_dir(name)
        return name

    def _keep_root(self):
        # A disk keeps the user's directory once its last entry goes; a key
        # prefix vanishes with it, and the walk takes a missing root for a
        # store it cannot see rather than an empty one.
        default_storage.make_dir(self._root())

    def _sync(self):
        return FileSyncService().sync_user_recursive(self.user)

    def _listing(self, *parts):
        """The names one directory of the tree holds, sorted."""
        name = "/".join([self._root(), *parts])
        if not default_storage.is_dir(name):
            return []
        directories, files = default_storage.listdir(name)
        return sorted([*directories, *files])

    def _live(self, name):
        return File.objects.filter(
            owner=self.user, name=name, deleted_at__isnull=True
        ).first()


class DiskToDbTests(SyncReconciliationTestCase):
    """Phase 1: nodes present on disk but missing in the DB get registered."""

    def test_registers_nested_tree_at_every_depth(self):
        self._write("top.txt")
        self._write("A", "mid.txt")
        self._write("A", "B", "deep.txt")

        result = self._sync()

        self.assertEqual(result.folders_created, 2)
        self.assertEqual(result.files_created, 3)
        self.assertEqual(result.errors, [])

        # Parentage must reflect the disk hierarchy, not a flat dump.
        folder_a = self._live("A")
        folder_b = self._live("B")
        self.assertIsNone(folder_a.parent_id)
        self.assertEqual(folder_b.parent_id, folder_a.pk)
        self.assertEqual(self._live("deep.txt").parent_id, folder_b.pk)
        self.assertEqual(self._live("mid.txt").parent_id, folder_a.pk)
        self.assertIsNone(self._live("top.txt").parent_id)

    def test_records_size_and_content_path_for_registered_files(self):
        self._write("A", "sized.bin", contents=b"0123456789")

        self._sync()

        f = self._live("sized.bin")
        self.assertEqual(f.size, 10)
        self.assertEqual(
            f.content.name,
            f"files/users/{self.user.username}/A/sized.bin",
        )

    def test_is_idempotent(self):
        self._write("A", "B", "deep.txt")

        self._sync()
        second = self._sync()

        self.assertEqual(second.files_created, 0)
        self.assertEqual(second.folders_created, 0)
        self.assertEqual(second.files_soft_deleted, 0)
        self.assertEqual(second.folders_soft_deleted, 0)
        self.assertEqual(
            File.objects.filter(owner=self.user, deleted_at__isnull=True).count(),
            3,
        )


class DbToDiskTests(SyncReconciliationTestCase):
    """Phase 2: DB rows whose disk counterpart vanished get soft-deleted."""

    def test_soft_deletes_file_missing_from_disk(self):
        path = self._write("gone.txt")
        self._sync()
        default_storage.delete(path)

        result = self._sync()

        self.assertEqual(result.files_soft_deleted, 1)
        self.assertIsNone(self._live("gone.txt"))
        self.assertIsNotNone(
            File.objects.get(owner=self.user, name="gone.txt").deleted_at
        )

    def test_soft_deletes_nested_file_missing_from_disk(self):
        path = self._write("A", "B", "deep.txt")
        self._sync()
        default_storage.delete(path)

        result = self._sync()

        self.assertEqual(result.files_soft_deleted, 1)
        self.assertIsNone(self._live("deep.txt"))
        # Ancestors survive - only the vanished node is reconciled.
        self.assertIsNotNone(self._live("A"))
        self.assertIsNotNone(self._live("B"))

    def test_soft_deleting_folder_cascades_to_descendants(self):
        self._write("A", "B", "deep.txt")
        self._sync()
        default_storage.delete_prefix(f"{self._root()}/A")

        result = self._sync()

        self.assertEqual(result.folders_soft_deleted, 1)
        for name in ("A", "B", "deep.txt"):
            self.assertIsNone(self._live(name), f"{name} should be soft-deleted")

    def test_soft_deletes_on_node_type_mismatch(self):
        # A path that was a file and is now a directory (or vice versa) is not
        # the same node - the stale row must go rather than silently mismatch.
        path = self._write("swap")
        self._sync()
        self.assertIsNotNone(self._live("swap"))

        default_storage.delete(path)
        self._mkdir("swap")
        result = self._sync()

        self.assertEqual(result.files_soft_deleted, 1)
        self.assertEqual(result.folders_created, 1)
        swap = self._live("swap")
        self.assertEqual(swap.node_type, File.NodeType.FOLDER)

    def test_a_file_that_became_a_directory_is_not_dragged_into_the_trash(self):
        # Trashing a file moves its blob out of the tree. The row still says
        # "file" while the path is now a directory, and moving that would
        # take content the row never owned along with it.
        path = self._write("swap")
        self._sync()

        default_storage.delete(path)
        self._mkdir("swap")
        inside = self._write("swap", "inside.txt", contents=b"KEEP")

        result = self._sync()

        self.assertEqual(result.files_soft_deleted, 1)
        self.assertEqual(result.folders_created, 1)
        with default_storage.open(inside, "rb") as fh:
            self.assertEqual(fh.read(), b"KEEP")
        self.assertFalse(default_storage.is_dir("trash"))

    def test_records_a_delete_event_for_sync_detected_removals(self):
        from workspace.files.models import FileEvent

        path = self._write("audited.txt")
        self._sync()
        default_storage.delete(path)

        self._sync()

        row = File.objects.get(owner=self.user, name="audited.txt")
        event = FileEvent.objects.filter(
            file=row, action=FileEvent.Action.DELETED
        ).first()
        self.assertIsNotNone(event)
        self.assertTrue(event.metadata.get("detected_by_sync"))


class TrashInteractionTests(SyncReconciliationTestCase):
    """Trashed rows must not be resurrected or duplicated by the walk."""

    def test_does_not_recreate_a_trashed_file_still_on_disk(self):
        self._write("trashed.txt")
        self._sync()
        row = self._live("trashed.txt")
        trash.trash_subtree(row)

        result = self._sync()

        self.assertEqual(result.files_created, 0)
        self.assertEqual(
            File.objects.filter(owner=self.user, name="trashed.txt").count(),
            1,
            "sync must not create a live duplicate alongside the trashed row",
        )

    def test_does_not_recreate_a_trashed_folder_still_on_disk(self):
        self._mkdir("Trashed")
        self._sync()
        trash.trash_subtree(self._live("Trashed"))

        result = self._sync()

        self.assertEqual(result.folders_created, 0)
        self.assertEqual(
            File.objects.filter(owner=self.user, name="Trashed").count(), 1
        )

    def test_does_not_descend_into_a_trashed_folder(self):
        # Children of a trashed folder stay trashed: descending would register
        # them as fresh live rows under a soft-deleted parent.
        self._write("Trashed", "inside.txt")
        self._sync()
        trash.trash_subtree(self._live("Trashed"))

        self._sync()

        self.assertIsNone(self._live("Trashed"))
        self.assertIsNone(self._live("inside.txt"))

    def test_registers_a_new_file_inside_a_folder_that_has_trashed_siblings(self):
        # A trashed sibling must not mask an unrelated new node at the same level.
        self._write("A", "old.txt")
        self._sync()
        trash.trash_subtree(self._live("old.txt"))

        self._write("A", "new.txt")
        result = self._sync()

        self.assertEqual(result.files_created, 1)
        self.assertIsNotNone(self._live("new.txt"))


class ScopeIsolationTests(SyncReconciliationTestCase):
    """The walk must stay inside the user's own personal-files scope."""

    def test_ignores_another_users_tree(self):
        other = User.objects.create_user(
            username="stranger", email="s@test.com", password="pass"
        )
        other_file = tree.create_folder(other, "StrangerFolder")

        self._write("mine.txt")
        self._sync()

        other_file.refresh_from_db()
        self.assertIsNone(other_file.deleted_at)
        self.assertEqual(
            File.objects.filter(owner=self.user, deleted_at__isnull=True).count(), 1
        )

    def test_missing_user_directory_is_a_no_op(self):
        result = self._sync()

        self.assertEqual(result.files_created, 0)
        self.assertEqual(result.folders_created, 0)
        self.assertEqual(result.files_soft_deleted, 0)
        self.assertEqual(result.errors, [])

    def test_skips_group_files(self):
        # Group-owned rows live under a different storage root; the personal
        # walk must not treat them as missing-on-disk and trash them.
        from django.contrib.auth.models import Group

        group = Group.objects.create(name="team")
        self.user.groups.add(group)
        group_folder = tree.create_folder(self.user, "TeamSpace", group=group)

        self._sync()

        group_folder.refresh_from_db()
        self.assertIsNone(group_folder.deleted_at)


class ShortcutCollisionTests(SyncReconciliationTestCase):
    """A disk entry under the name of a live shortcut takes the name.

    The walk matches rows to disk entries by name, so a disk entry registered
    under any other name would read as gone from the disk on the next run.
    The shortcut has no name on disk to keep in step: it is the one renamed.
    """

    def setUp(self):
        super().setUp()
        self.target = tree.create_file(
            self.user, "target.txt", content=ContentFile(b"target", name="t.txt")
        )

    def _shortcut(self, name, parent=None):
        return create_shortcut(self.user, self.target, parent, name=name)

    def test_a_file_on_disk_takes_the_name_of_a_shortcut(self):
        shortcut = self._shortcut("report.pdf")
        self._write("report.pdf", contents=b"%PDF-1.4 from disk")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.files_created, 1)
        self.assertEqual(result.shortcuts_renamed, 1)
        registered = self._live("report.pdf")
        self.assertEqual(registered.node_type, File.NodeType.FILE)
        self.assertEqual(
            registered.content.name, f"files/users/{self.user.username}/report.pdf"
        )
        shortcut.refresh_from_db()
        self.assertEqual(shortcut.name, "report (Copy).pdf")
        self.assertIsNone(shortcut.deleted_at)
        self.assertEqual(shortcut.shortcut_target_id, self.target.pk)
        self.assertTrue(
            FileEvent.objects.filter(
                file=shortcut,
                action=FileEvent.Action.RENAMED,
                metadata__new_name="report (Copy).pdf",
            ).exists()
        )

    def test_the_next_run_leaves_both_alone(self):
        shortcut = self._shortcut("report.pdf")
        self._write("report.pdf")
        self._sync()

        second = self._sync()

        self.assertEqual(second.errors, [])
        self.assertEqual(second.files_created, 0)
        self.assertEqual(second.files_soft_deleted, 0)
        self.assertEqual(second.shortcuts_renamed, 0)
        self.assertIsNotNone(self._live("report.pdf"))
        shortcut.refresh_from_db()
        self.assertEqual(shortcut.name, "report (Copy).pdf")
        self.assertIsNone(shortcut.deleted_at)

    def test_names_clash_caselessly(self):
        shortcut = self._shortcut("REPORT.pdf")
        self._write("report.pdf")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertIsNotNone(self._live("report.pdf"))
        shortcut.refresh_from_db()
        self.assertEqual(shortcut.name, "REPORT (Copy).pdf")

    def test_a_directory_on_disk_takes_the_name_and_is_walked(self):
        docs = tree.create_folder(self.user, "Docs")
        shortcut = self._shortcut("Archive", parent=docs)
        self._write("Docs", "Archive", "inside.txt")

        result = self._sync()

        self.assertEqual(result.errors, [])
        archive = self._live("Archive")
        self.assertEqual(archive.node_type, File.NodeType.FOLDER)
        self.assertEqual(archive.parent_id, docs.pk)
        self.assertEqual(self._live("inside.txt").parent_id, archive.pk)
        shortcut.refresh_from_db()
        self.assertEqual(shortcut.name, "Archive (Copy)")

    def test_the_new_name_is_not_taken_from_another_disk_entry(self):
        # Unregistered as well: the shortcut must not move onto a name the
        # same walk is about to register.
        shortcut = self._shortcut("report.pdf")
        self._write("report.pdf")
        self._write("report (Copy).pdf")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.files_created, 2)
        self.assertEqual(result.shortcuts_renamed, 1)
        self.assertIsNotNone(self._live("report.pdf"))
        self.assertIsNotNone(self._live("report (Copy).pdf"))
        shortcut.refresh_from_db()
        self.assertEqual(shortcut.name, "report (Copy 2).pdf")

    def test_a_shallow_sync_of_the_folder_does_the_same(self):
        docs = tree.create_folder(self.user, "Docs")
        shortcut = self._shortcut("notes.md", parent=docs)
        self._write("Docs", "notes.md")

        result = FileSyncService().sync_folder_shallow(self.user, docs)

        self.assertEqual(result.errors, [])
        self.assertEqual(self._live("notes.md").node_type, File.NodeType.FILE)
        shortcut.refresh_from_db()
        self.assertEqual(shortcut.name, "notes (Copy).md")

    def test_a_dry_run_reports_the_rename_and_changes_nothing(self):
        shortcut = self._shortcut("report.pdf")
        self._write("report.pdf")

        result = FileSyncService(dry_run=True).sync_user_recursive(self.user)

        self.assertEqual(result.files_created, 1)
        self.assertEqual(result.shortcuts_renamed, 1)
        shortcut.refresh_from_db()
        self.assertEqual(shortcut.name, "report.pdf")
        self.assertFalse(
            File.objects.filter(
                owner=self.user, node_type=File.NodeType.FILE, name="report.pdf"
            ).exists()
        )


class CaselessDuplicateTests(SyncReconciliationTestCase):
    """Disk entries whose names differ only in case or accent encoding.

    A case-sensitive disk (Linux) holds ``Report.txt`` and ``report.txt``
    side by side, and one that does not normalize names (Linux, Windows)
    holds both encodings of ``café``; a folder holds one live node per
    caseless name. The node that holds the name keeps it, the newcomer is
    renamed to ``name (Copy N)`` on disk and registered under that name, the
    way migrations 0063 and 0066 resolved the same clashes in the database.
    """

    def _write_clashing(self, *names, contents=None):
        contents = contents or [b"data"] * len(names)
        before = len(self._listing())
        for name, data in zip(names, contents, strict=True):
            self._write(name, contents=data)
        if len(self._listing()) < before + len(names):
            self.skipTest("this file system folds the names into one")

    def _on_disk(self):
        return self._listing()

    def test_a_newcomer_is_renamed_on_disk_and_registered(self):
        self._write("Report.txt", contents=b"first")
        self._sync()
        kept = self._live("Report.txt")
        self._write_clashing("report.txt", contents=[b"second"])

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.files_created, 1)
        self.assertEqual(result.disk_entries_renamed, 1)
        self.assertEqual(self._on_disk(), ["Report.txt", "report (Copy).txt"])
        newcomer = self._live("report (Copy).txt")
        self.assertEqual(
            newcomer.content.name,
            f"files/users/{self.user.username}/report (Copy).txt",
        )
        self.assertEqual(newcomer.size, len(b"second"))
        with newcomer.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"second")
        self.assertEqual(self._live("Report.txt").pk, kept.pk)

    def test_the_next_run_leaves_both_alone(self):
        self._write("Report.txt")
        self._sync()
        self._write_clashing("report.txt")
        self._sync()

        second = self._sync()

        self.assertEqual(second.errors, [])
        self.assertEqual(second.files_created, 0)
        self.assertEqual(second.files_soft_deleted, 0)
        self.assertEqual(second.disk_entries_renamed, 0)
        self.assertIsNotNone(self._live("Report.txt"))
        self.assertIsNotNone(self._live("report (Copy).txt"))

    def test_two_newcomers_of_one_run_keep_one_name(self):
        self._write_clashing("Report.txt", "report.txt")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.files_created, 2)
        self.assertEqual(result.disk_entries_renamed, 1)
        # By name order, so every run of every instance picks the same one.
        self.assertEqual(self._on_disk(), ["Report.txt", "report (Copy).txt"])
        self.assertIsNotNone(self._live("Report.txt"))
        self.assertIsNotNone(self._live("report (Copy).txt"))

    def test_the_new_name_is_not_taken_from_another_disk_entry(self):
        self._write("Report.txt")
        self._sync()
        self._write_clashing("report.txt", "report (Copy).txt")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.files_created, 2)
        self.assertEqual(
            self._on_disk(),
            ["Report.txt", "report (Copy 2).txt", "report (Copy).txt"],
        )
        self.assertIsNotNone(self._live("report (Copy 2).txt"))

    def test_a_directory_is_renamed_and_walked_in_the_same_run(self):
        self._mkdir("Docs")
        self._sync()
        self._write("docs", "inside.txt")
        if len(self._on_disk()) < 2:
            self.skipTest("this file system folds the names into one")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.folders_created, 1)
        self.assertEqual(result.files_created, 1)
        self.assertEqual(self._on_disk(), ["Docs", "docs (Copy)"])
        renamed = self._live("docs (Copy)")
        self.assertEqual(renamed.node_type, File.NodeType.FOLDER)
        inside = self._live("inside.txt")
        self.assertEqual(inside.parent_id, renamed.pk)
        self.assertEqual(
            inside.content.name,
            f"files/users/{self.user.username}/docs (Copy)/inside.txt",
        )

    def test_accent_encodings_clash_like_case(self):
        composed = unicodedata.normalize("NFC", "café.txt")
        decomposed = unicodedata.normalize("NFD", "café.txt")
        self._write(composed)
        self._sync()
        self._write_clashing(decomposed)

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.disk_entries_renamed, 1)
        self.assertIsNotNone(self._live(composed))
        # Composed, like every name the file service stores.
        self.assertIsNotNone(
            self._live(unicodedata.normalize("NFC", "café (Copy).txt"))
        )

    def test_a_row_that_could_not_be_trashed_renames_nothing_on_disk(self):
        # The stale row still holds the name, for this run only: renaming
        # the directory aside would answer a failure that the next run clears.
        path = self._write("swap")
        self._sync()
        default_storage.delete(path)
        self._write("swap", "inside.txt")

        with patch.object(trash, "trash_subtree", side_effect=OSError("busy")):
            result = self._sync()

        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.disk_entries_renamed, 0)
        self.assertEqual(result.folders_created, 0)
        self.assertEqual(self._on_disk(), ["swap"])

        again = self._sync()

        self.assertEqual(again.errors, [])
        self.assertEqual(again.files_soft_deleted, 1)
        self.assertEqual(again.folders_created, 1)
        self.assertEqual(self._live("swap").node_type, File.NodeType.FOLDER)

    def test_a_dry_run_reports_the_rename_and_changes_nothing(self):
        self._write("Report.txt")
        self._sync()
        self._write_clashing("report.txt")

        result = FileSyncService(dry_run=True).sync_user_recursive(self.user)

        self.assertEqual(result.files_created, 1)
        self.assertEqual(result.disk_entries_renamed, 1)
        self.assertEqual(self._on_disk(), ["Report.txt", "report.txt"])
        self.assertEqual(
            File.objects.filter(owner=self.user, deleted_at__isnull=True).count(), 1
        )


class FollowedRenameTests(SyncReconciliationTestCase):
    """A disk entry renamed only in case or accent encoding is the same node.

    Its row follows the new name instead of going to the trash: on a disk
    that folds names (Windows, macOS) both names are one file, and trashing
    the row would carry the bytes into the trash with it. On any disk the
    node keeps what hangs on its row - shares, tags, comments, history.
    """

    def _rename(self, old, new, *parent):
        default_storage.move(
            "/".join([self._root(), *parent, old]),
            "/".join([self._root(), *parent, new]),
        )

    def _on_disk(self, *parts):
        return self._listing(*parts)

    def test_the_row_of_a_file_follows_its_new_case(self):
        self._write("Report.txt", contents=b"kept")
        self._sync()
        row = self._live("Report.txt")
        self._rename("Report.txt", "report.txt")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.renames_followed, 1)
        self.assertEqual(result.files_soft_deleted, 0)
        self.assertEqual(result.files_created, 0)
        row.refresh_from_db()
        self.assertIsNone(row.deleted_at)
        self.assertEqual(row.name, "report.txt")
        self.assertEqual(
            row.content.name, f"files/users/{self.user.username}/report.txt"
        )
        with row.content.open("rb") as handle:
            self.assertEqual(handle.read(), b"kept")
        self.assertEqual(self._on_disk(), ["report.txt"])
        self.assertTrue(
            FileEvent.objects.filter(file=row, action=FileEvent.Action.RENAMED).exists()
        )

    def test_the_next_run_leaves_it_alone(self):
        self._write("Report.txt")
        self._sync()
        self._rename("Report.txt", "report.txt")
        self._sync()

        second = self._sync()

        self.assertEqual(second.errors, [])
        self.assertEqual(second.renames_followed, 0)
        self.assertEqual(second.files_soft_deleted, 0)
        self.assertEqual(second.files_created, 0)

    def test_the_subtree_of_a_folder_follows_it(self):
        self._write("Docs", "a.txt")
        self._write("Docs", "Sub", "b.txt")
        self._sync()
        folder = self._live("Docs")
        self._rename("Docs", "docs")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.renames_followed, 1)
        self.assertEqual(result.folders_soft_deleted, 0)
        self.assertEqual(result.files_created + result.folders_created, 0)
        folder.refresh_from_db()
        self.assertEqual(folder.name, "docs")
        prefix = f"files/users/{self.user.username}/docs"
        self.assertEqual(self._live("a.txt").content.name, f"{prefix}/a.txt")
        self.assertEqual(self._live("b.txt").content.name, f"{prefix}/Sub/b.txt")
        self.assertEqual(self._live("b.txt").path, "docs/Sub/b.txt")
        again = self._sync()
        self.assertEqual(again.errors, [])
        self.assertEqual(again.files_soft_deleted + again.folders_soft_deleted, 0)

    def test_a_folder_gone_below_a_followed_one_takes_its_children_along(self):
        # The walk renames the folder from rows it loaded before: trashing a
        # folder below it builds the subtree from that folder's path, which
        # has to be the new one. A non-ASCII case, because SQLite's LIKE
        # would match a stale prefix that differs in ASCII case only.
        self._write("Été", "Sub", "b.txt")
        self._sync()
        self._rename("Été", "été")
        default_storage.delete_prefix(f"{self._root()}/été/Sub")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.renames_followed, 1)
        self.assertEqual(result.folders_soft_deleted, 1)
        self.assertIsNotNone(self._live("été"))
        self.assertIsNone(self._live("Sub"))
        self.assertIsNone(self._live("b.txt"))

    def test_the_other_encoding_of_its_name_is_respelled_on_disk(self):
        # Names are stored composed: the row keeps its name, and the entry
        # goes back under it rather than the row taking the decomposed one.
        composed = unicodedata.normalize("NFC", "Café")
        decomposed = unicodedata.normalize("NFD", "Café")
        self._write(composed, "Sub", "b.txt")
        self._sync()
        folder = self._live(composed)
        self._rename(composed, decomposed)
        if self._on_disk() != [decomposed]:
            self.skipTest("this file system normalizes names")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.renames_followed, 0)
        self.assertEqual(result.disk_entries_renamed, 1)
        self.assertEqual(result.folders_soft_deleted + result.files_soft_deleted, 0)
        self.assertEqual(self._on_disk(), [composed])
        folder.refresh_from_db()
        self.assertEqual(folder.name, composed)
        self.assertIsNone(folder.deleted_at)
        self.assertEqual(
            self._live("b.txt").content.name,
            f"files/users/{self.user.username}/{composed}/Sub/b.txt",
        )

    def test_of_two_new_spellings_the_first_by_name_is_followed(self):
        self._write("Report.txt")
        self._sync()
        row = self._live("Report.txt")
        default_storage.delete(f"{self._root()}/Report.txt")
        self._write("report.txt")
        self._write("REPORT.txt")
        if len(self._on_disk()) < 2:
            self.skipTest("this file system folds the names into one")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.renames_followed, 1)
        self.assertEqual(result.disk_entries_renamed, 1)
        row.refresh_from_db()
        self.assertEqual(row.name, "REPORT.txt")
        self.assertIsNotNone(self._live("report (Copy).txt"))

    def test_a_rename_that_fails_leaves_the_row_and_the_disk_alone(self):
        # Trashing the row instead would carry the bytes along on a disk
        # that folds names, where both names are the one file.
        self._write("Report.txt", contents=b"kept")
        self._sync()
        row = self._live("Report.txt")
        self._rename("Report.txt", "report.txt")

        with patch.object(tree, "rename", side_effect=OSError("busy")):
            result = self._sync()

        self.assertEqual(len(result.errors), 1)
        self.assertEqual(result.renames_followed, 0)
        self.assertEqual(result.files_soft_deleted, 0)
        self.assertEqual(result.files_created, 0)
        self.assertEqual(result.disk_entries_renamed, 0)
        row.refresh_from_db()
        self.assertIsNone(row.deleted_at)
        self.assertEqual(row.name, "Report.txt")
        self.assertEqual(self._on_disk(), ["report.txt"])
        self.assertEqual(self._sync().renames_followed, 1)

    def test_a_rename_that_changes_the_node_type_is_not_followed(self):
        self._mkdir("Docs")
        self._sync()
        folder = self._live("Docs")
        default_storage.remove_dir_if_empty(f"{self._root()}/Docs")
        self._write("docs")

        result = self._sync()

        self.assertEqual(result.errors, [])
        self.assertEqual(result.renames_followed, 0)
        self.assertEqual(result.folders_soft_deleted, 1)
        self.assertEqual(result.files_created, 1)
        folder.refresh_from_db()
        self.assertIsNotNone(folder.deleted_at)

    def test_a_dry_run_reports_the_rename_and_changes_nothing(self):
        self._write("Report.txt")
        self._sync()
        self._rename("Report.txt", "report.txt")

        result = FileSyncService(dry_run=True).sync_user_recursive(self.user)

        self.assertEqual(result.renames_followed, 1)
        self.assertEqual(result.files_soft_deleted, 0)
        self.assertEqual(result.files_created, 0)
        self.assertEqual(result.disk_entries_renamed, 0)
        self.assertIsNotNone(self._live("Report.txt"))


class RenameOnDiskTests(SyncReconciliationTestCase):
    """Renaming a disk entry aside never replaces what holds the new name.

    The name is picked from a scan that is already stale when the rename
    runs: a writer can put a file there in between. Only a disk claims the
    name in the same step as the rename; object storage checks first.
    """

    def _rename(self, old, new):
        FileSyncService._rename_on_disk(self._root(), old, new)

    def _read(self, *parts):
        with default_storage.open("/".join([self._root(), *parts]), "rb") as handle:
            return handle.read()

    @local_storage_only
    def test_a_file_written_after_the_check_is_not_replaced(self):
        self._write("a.txt", contents=b"newcomer")
        self._write("a (Copy).txt", contents=b"theirs")

        # The writer lands between any existence check and the rename.
        with patch("os.path.lexists", return_value=False):
            with self.assertRaises(FileExistsError):
                self._rename("a.txt", "a (Copy).txt")

        self.assertEqual(self._read("a (Copy).txt"), b"theirs")
        self.assertEqual(self._read("a.txt"), b"newcomer")

    def test_a_free_name_is_taken_and_the_old_one_released(self):
        self._write("a.txt", contents=b"newcomer")

        self._rename("a.txt", "a (Copy).txt")

        self.assertEqual(self._listing(), ["a (Copy).txt"])
        self.assertEqual(self._read("a (Copy).txt"), b"newcomer")

    @local_storage_only
    def test_without_hard_links_a_file_stays_where_it_is(self):
        # No other call renames a file without replacing what holds the name.
        self._write("a.txt", contents=b"newcomer")

        with patch("os.link", side_effect=PermissionError("no hard links")):
            with self.assertRaises(OSError):
                self._rename("a.txt", "a (Copy).txt")

        self.assertEqual(self._listing(), ["a.txt"])

    def test_a_directory_is_not_renamed_onto_an_existing_entry(self):
        self._write("d", "inside.txt")
        self._write("d (Copy)", contents=b"theirs")

        with self.assertRaises(FileExistsError):
            self._rename("d", "d (Copy)")

        self.assertEqual(self._read("d (Copy)"), b"theirs")
        self.assertEqual(self._read("d", "inside.txt"), b"data")

    @local_storage_only
    def test_an_empty_directory_made_after_the_check_is_not_replaced(self):
        # rename(2) replaces an empty directory without a word.
        self._write("d", "inside.txt")
        self._mkdir("d (Copy)")

        with patch("os.path.lexists", return_value=False):
            with self.assertRaises(FileExistsError):
                self._rename("d", "d (Copy)")

        self.assertEqual(self._listing("d (Copy)"), [])
        self.assertEqual(self._read("d", "inside.txt"), b"data")

    @local_storage_only
    def test_a_directory_rename_that_fails_releases_the_name(self):
        self._write("d", "inside.txt")

        with patch("os.rename", side_effect=OSError("busy")):
            with self.assertRaises(OSError):
                self._rename("d", "d (Copy)")

        self.assertEqual(self._listing(), ["d"])


class SyncEndpointTests(SyncReconciliationTestCase):
    def test_the_response_reports_every_counter(self):
        target = tree.create_file(
            self.user, "target.txt", content=ContentFile(b"target", name="t.txt")
        )
        create_shortcut(self.user, target, None, name="report.pdf")
        self._write("report.pdf")
        self.client.force_login(self.user)

        response = self.client.post("/api/v1/files/sync")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["files_created"], 1)
        self.assertEqual(body["shortcuts_renamed"], 1)
        self.assertEqual(body["disk_entries_renamed"], 0)
        self.assertEqual(body["renames_followed"], 0)
        self.assertEqual(body["error_count"], 0)


class SyncQueryBudgetTests(SyncReconciliationTestCase):
    """The steady-state walk must not scale its query count with the tree.

    Reconciliation runs for every active user on a beat schedule, so a
    per-folder query count multiplies by (users x folders) on a large
    instance. These tests pin the budget to a constant so a future change
    that reintroduces a per-level lookup fails here instead of in prod.
    """

    def _build_wide_tree(self, folders, files_per_folder=2):
        for i in range(folders):
            for j in range(files_per_folder):
                self._write(f"dir{i}", f"file{j}.txt")

    def _warm_sync_queries(self):
        """Query count for a sync that finds nothing to do."""
        with CaptureQueriesContext(connection) as ctx:
            result = FileSyncService().sync_user_recursive(self.user)
        self.assertEqual(result.files_created, 0)
        self.assertEqual(result.folders_created, 0)
        self.assertEqual(result.files_soft_deleted, 0)
        self.assertEqual(result.folders_soft_deleted, 0)
        return len(ctx.captured_queries)

    def test_steady_state_query_count_is_independent_of_tree_size(self):
        self._build_wide_tree(folders=3)
        self._sync()
        small = self._warm_sync_queries()

        self._build_wide_tree(folders=12)
        self._sync()
        large = self._warm_sync_queries()

        self.assertEqual(
            small,
            large,
            "sync spends queries proportional to the tree: a 4x wider tree "
            f"changed the warm query count from {small} to {large}. The walk "
            "must read the subtree once per user, not once per folder.",
        )

    def test_steady_state_walk_stays_within_a_small_constant_budget(self):
        self._build_wide_tree(folders=10)
        self._sync()

        queries = self._warm_sync_queries()

        self.assertLessEqual(
            queries,
            4,
            f"warm sync of an unchanged 10-folder tree spent {queries} "
            "queries; it should read live + trashed rows once per user.",
        )

    def test_deep_tree_does_not_scale_queries_with_depth(self):
        self._write("d1", "d2", "d3", "d4", "d5", "leaf.txt")
        self._sync()

        queries = self._warm_sync_queries()

        self.assertLessEqual(
            queries,
            4,
            f"warm sync of a 5-deep chain spent {queries} queries; depth "
            "must not add per-level lookups.",
        )
