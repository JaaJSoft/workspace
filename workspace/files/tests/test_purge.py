"""Emptying the trash deletes in bounded batches, never the whole tree at once.

``File`` has ``pre_delete`` receivers, so the delete collector loads every row
it deletes (and every descendant the cascade reaches) before the first
``DELETE``. The peak number of rows it holds is what these tests pin: the
receivers below see a batch's ``pre_delete`` signals all fire before any of
its ``post_delete`` ones.
"""

import os
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models.signals import post_delete, pre_delete
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.files import tasks as files_tasks
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services._trash import trash_dir

User = get_user_model()

BATCH_SIZE = 25


@contextmanager
def collector_peak():
    """Yield a dict whose ``peak`` is the most File rows held by one collector."""
    state = {"pending": 0, "peak": 0}

    def on_pre_delete(sender, **kwargs):
        state["pending"] += 1
        state["peak"] = max(state["peak"], state["pending"])

    def on_post_delete(sender, **kwargs):
        state["pending"] -= 1

    pre_delete.connect(on_pre_delete, sender=File, weak=False)
    post_delete.connect(on_post_delete, sender=File, weak=False)
    try:
        yield state
    finally:
        pre_delete.disconnect(on_pre_delete, sender=File)
        post_delete.disconnect(on_post_delete, sender=File)


@mock.patch("workspace.files.services.purge.PURGE_BATCH_SIZE", BATCH_SIZE)
class PurgeBatchingTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="purger", password="pass")
        self.client.force_authenticate(user=self.user)

    def _build_tree(self, name="Big"):
        """A trashed folder holding 110 rows over three levels, with blobs."""
        root = FileService.create_folder(self.user, name)
        for i in range(5):
            sub = FileService.create_folder(self.user, f"sub{i}", root)
            deep = FileService.create_folder(self.user, "deep", sub)
            for parent in (sub, deep):
                for j in range(10):
                    FileService.create_file(
                        self.user,
                        f"f{j}.txt",
                        parent,
                        content=ContentFile(b"bytes", name=f"f{j}.txt"),
                    )
        FileService.soft_delete(root, acting_user=self.user)
        root.refresh_from_db()
        return root

    def _trash_dir_on_disk(self, root):
        return os.path.join(settings.MEDIA_ROOT, trash_dir(root))

    def test_emptying_the_trash_deletes_in_bounded_batches(self):
        root = self._build_tree()
        tree_size = File.objects.filter(owner=self.user).count()
        self.assertEqual(tree_size, 111)
        self.assertTrue(os.path.isdir(self._trash_dir_on_disk(root)))

        with collector_peak() as state:
            response = self.client.delete("/api/v1/files/trash/clean?force=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["deleted"], tree_size)
        self.assertLessEqual(state["peak"], BATCH_SIZE)
        self.assertFalse(File.objects.filter(owner=self.user).exists())
        self.assertFalse(os.path.exists(self._trash_dir_on_disk(root)))

    def test_retention_clean_leaves_recent_trash_alone(self):
        old = self._build_tree("Old")
        File.objects.filter(owner=self.user).update(
            deleted_at=timezone.now() - timedelta(days=90)
        )
        recent = self._build_tree("Recent")

        with collector_peak() as state:
            response = self.client.delete("/api/v1/files/trash/clean")

        self.assertEqual(response.data["deleted"], 111)
        self.assertLessEqual(state["peak"], BATCH_SIZE)
        self.assertFalse(File.objects.filter(pk=old.pk).exists())
        self.assertEqual(File.objects.filter(owner=self.user).count(), 111)
        self.assertTrue(File.objects.filter(pk=recent.pk).exists())

    def test_purging_a_folder_deletes_its_subtree_in_bounded_batches(self):
        root = self._build_tree()

        with collector_peak() as state:
            response = self.client.delete(f"/api/v1/files/{root.uuid}/purge")

        self.assertEqual(response.status_code, 204)
        self.assertLessEqual(state["peak"], BATCH_SIZE)
        self.assertFalse(File.objects.filter(owner=self.user).exists())
        self.assertFalse(os.path.exists(self._trash_dir_on_disk(root)))

    def test_purging_a_folder_spares_a_trashed_folder_with_the_same_path(self):
        first = FileService.create_folder(self.user, "Docs")
        FileService.create_file(self.user, "a.txt", first)
        FileService.soft_delete(first, acting_user=self.user)
        second = FileService.create_folder(self.user, "Docs")
        kept = FileService.create_file(self.user, "b.txt", second)
        FileService.soft_delete(second, acting_user=self.user)

        FileService.hard_delete(first, acting_user=self.user)

        self.assertFalse(File.objects.filter(pk=first.pk).exists())
        self.assertTrue(File.objects.filter(pk=second.pk).exists())
        self.assertTrue(File.objects.filter(pk=kept.pk).exists())

    @override_settings(TRASH_RETENTION_DAYS=30)
    def test_retention_task_deletes_in_bounded_batches(self):
        self._build_tree()
        File.objects.filter(owner=self.user).update(
            deleted_at=timezone.now() - timedelta(days=45)
        )

        with collector_peak() as state:
            result = files_tasks.purge_trash.run()

        self.assertEqual(result["folders_deleted"], 11)
        self.assertEqual(result["files_deleted"], 100)
        self.assertLessEqual(state["peak"], BATCH_SIZE)
        self.assertFalse(File.objects.filter(owner=self.user).exists())
