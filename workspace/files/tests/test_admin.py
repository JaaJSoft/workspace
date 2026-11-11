from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.files.models import File, ProcessingFailure
from workspace.files.services import storage_ops, trash, tree

User = get_user_model()


class FilesAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        cls.file = File.objects.create(
            owner=cls.admin, name="broken.jpg", node_type=File.NodeType.FILE
        )
        cls.failure = ProcessingFailure.objects.create(
            file=cls.file,
            processor="thumbnails",
            attempts=3,
            last_attempt_at=timezone.now(),
        )

    def setUp(self):
        self.client.force_login(self.admin)

    def test_file_change_list_renders(self):
        response = self.client.get(reverse("admin:files_file_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "broken.jpg")

    def test_retry_action_unparks_the_selection_only(self):
        other_file = File.objects.create(
            owner=self.admin, name="also-broken.jpg", node_type=File.NodeType.FILE
        )
        other_failure = ProcessingFailure.objects.create(
            file=other_file,
            processor="thumbnails",
            attempts=1,
            last_attempt_at=timezone.now(),
        )

        with patch("workspace.files.tasks.catch_up_file.apply_async") as queue:
            response = self.client.post(
                reverse("admin:files_processingfailure_changelist"),
                {
                    "action": "retry_processing",
                    "_selected_action": [str(self.failure.uuid)],
                },
            )
        self.assertEqual(response.status_code, 302)
        self.assertQuerySetEqual(ProcessingFailure.objects.all(), [other_failure])
        self.assertEqual(
            [c.kwargs["args"] for c in queue.call_args_list],
            [["thumbnails", str(self.failure.file_id)]],
        )

    def test_failure_rows_cannot_be_added_by_hand(self):
        self.assertEqual(
            self.client.get(reverse("admin:files_processingfailure_add")).status_code,
            403,
        )


class FileAdminDeletionTests(TestCase):
    """Both of the admin's delete paths remove a node for good."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        self.client.force_login(self.admin)
        self.folder = tree.create_folder(self.admin, "Docs")
        self.child = tree.create_file(
            self.admin,
            "report.txt",
            self.folder,
            content=ContentFile(b"bytes", name="report.txt"),
        )

    def _confirm_delete(self, node):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("admin:files_file_delete", args=[node.pk]), {"post": "yes"}
            )

    def _confirm_delete_selected(self, *nodes):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse("admin:files_file_changelist"),
                {
                    "action": "delete_selected",
                    "_selected_action": [str(node.pk) for node in nodes],
                    "post": "yes",
                },
            )

    def assertGoneForGood(self, node, blob=None):
        self.assertFalse(File.objects.filter(pk=node.pk).exists())
        if blob:
            self.assertFalse(default_storage.exists(blob))
            self.assertFalse(default_storage.exists(storage_ops.trash_node_path(node)))

    def test_delete_button_removes_the_subtree_for_good(self):
        blob = self.child.content.name

        response = self._confirm_delete(self.folder)

        self.assertEqual(response.status_code, 302)
        self.assertGoneForGood(self.folder)
        self.assertGoneForGood(self.child, blob)

    def test_delete_button_purges_a_trashed_node(self):
        with self.captureOnCommitCallbacks(execute=True):
            trash.soft_delete(self.child)
        trashed_blob = storage_ops.trash_node_path(self.child)
        self.assertTrue(default_storage.is_file(trashed_blob))

        response = self._confirm_delete(self.child)

        self.assertEqual(response.status_code, 302)
        self.assertGoneForGood(self.child, trashed_blob)
        self.assertTrue(File.objects.filter(pk=self.folder.pk).exists())

    def test_delete_selected_removes_the_subtrees_for_good(self):
        blob = self.child.content.name
        loose = tree.create_file(
            self.admin, "loose.txt", content=ContentFile(b"bytes", name="loose.txt")
        )
        loose_blob = loose.content.name
        kept = tree.create_file(
            self.admin, "kept.txt", content=ContentFile(b"bytes", name="kept.txt")
        )

        response = self._confirm_delete_selected(self.child, self.folder, loose)

        self.assertEqual(response.status_code, 302)
        self.assertGoneForGood(self.folder)
        self.assertGoneForGood(self.child, blob)
        self.assertGoneForGood(loose, loose_blob)
        self.assertTrue(default_storage.is_file(kept.content.name))
