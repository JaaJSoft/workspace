"""Behavioral contract for the disk <-> DB reconciliation walk.

``test_sync.py`` pins the storage prefix; this module pins *what the walk
decides* - which nodes get created, which get soft-deleted, which are left
alone - plus the query budget it spends doing so.

The query-count test is the load-bearing one: the walk is driven by celery
beat across every active user's whole tree, so a regression from a constant
number of queries per user back to a per-folder number is invisible in
correctness terms and very visible in production.
"""

import os
import shutil

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.files.models import File
from workspace.files.services import FileService
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
        return os.path.join(self.media_root, "files", "users", self.user.username)

    def _write(self, *parts, contents=b"data"):
        full = os.path.join(self._root(), *parts)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as fh:
            fh.write(contents)
        return full

    def _mkdir(self, *parts):
        full = os.path.join(self._root(), *parts)
        os.makedirs(full, exist_ok=True)
        return full

    def _sync(self):
        return FileSyncService().sync_user_recursive(self.user)

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
        os.remove(path)

        result = self._sync()

        self.assertEqual(result.files_soft_deleted, 1)
        self.assertIsNone(self._live("gone.txt"))
        self.assertIsNotNone(
            File.objects.get(owner=self.user, name="gone.txt").deleted_at
        )

    def test_soft_deletes_nested_file_missing_from_disk(self):
        path = self._write("A", "B", "deep.txt")
        self._sync()
        os.remove(path)

        result = self._sync()

        self.assertEqual(result.files_soft_deleted, 1)
        self.assertIsNone(self._live("deep.txt"))
        # Ancestors survive - only the vanished node is reconciled.
        self.assertIsNotNone(self._live("A"))
        self.assertIsNotNone(self._live("B"))

    def test_soft_deleting_folder_cascades_to_descendants(self):
        self._write("A", "B", "deep.txt")
        self._sync()
        shutil.rmtree(os.path.join(self._root(), "A"))

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

        os.remove(path)
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
        with self.settings(MEDIA_ROOT=self.media_root):
            path = self._write("swap")
            self._sync()

            os.remove(path)
            self._mkdir("swap")
            inside = self._write("swap", "inside.txt", contents=b"KEEP")

            result = self._sync()

            self.assertEqual(result.files_soft_deleted, 1)
            self.assertEqual(result.folders_created, 1)
            self.assertTrue(os.path.isfile(inside))
            with open(inside, "rb") as fh:
                self.assertEqual(fh.read(), b"KEEP")
            self.assertFalse(os.path.exists(os.path.join(self.media_root, "trash")))

    def test_records_a_delete_event_for_sync_detected_removals(self):
        from workspace.files.models import FileEvent

        path = self._write("audited.txt")
        self._sync()
        os.remove(path)

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
        row.soft_delete()

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
        self._live("Trashed").soft_delete()

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
        self._live("Trashed").soft_delete()

        self._sync()

        self.assertIsNone(self._live("Trashed"))
        self.assertIsNone(self._live("inside.txt"))

    def test_registers_a_new_file_inside_a_folder_that_has_trashed_siblings(self):
        # A trashed sibling must not mask an unrelated new node at the same level.
        self._write("A", "old.txt")
        self._sync()
        self._live("old.txt").soft_delete()

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
        other_file = FileService.create_folder(other, "StrangerFolder")

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
        group_folder = FileService.create_folder(self.user, "TeamSpace", group=group)

        self._sync()

        group_folder.refresh_from_db()
        self.assertIsNone(group_folder.deleted_at)


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
