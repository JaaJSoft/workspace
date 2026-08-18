"""Uploading into a folder that already holds a file with the same name."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services._storage_ops import unique_copy_name
from workspace.notifications.models import Notification

User = get_user_model()


class UniqueCopyNameTests(TestCase):
    def test_is_case_insensitive(self):
        self.assertEqual(
            unique_copy_name("Report.pdf", File.NodeType.FILE, {"report.pdf"}),
            "Report (Copy).pdf",
        )
        self.assertEqual(
            unique_copy_name(
                "Report.pdf", File.NodeType.FILE, {"report.pdf", "report (copy).pdf"}
            ),
            "Report (Copy 2).pdf",
        )

    def test_free_name_is_kept(self):
        self.assertEqual(
            unique_copy_name("new.txt", File.NodeType.FILE, {"other.txt"}), "new.txt"
        )


class NameConflictHelperTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.other = User.objects.create_user(username="bob", password="pass")

    def test_find_name_conflict_ignores_case_and_trash(self):
        existing = FileService.create_file(self.user, "Report.pdf")
        self.assertEqual(
            FileService.find_name_conflict(self.user, None, "report.PDF"), existing
        )
        FileService.soft_delete(existing, acting_user=self.user)
        self.assertIsNone(FileService.find_name_conflict(self.user, None, "report.pdf"))

    def test_conflicts_are_scoped_to_the_owner_outside_groups(self):
        FileService.create_file(self.other, "report.pdf")
        self.assertIsNone(FileService.find_name_conflict(self.user, None, "report.pdf"))

    def test_conflicts_are_scoped_to_the_group_inside_group_folders(self):
        group = Group.objects.create(name="Marketing")
        self.user.groups.add(group)
        self.other.groups.add(group)
        root = FileService.create_folder(self.user, "Marketing Files", group=group)
        theirs = FileService.create_file(self.other, "report.pdf", parent=root)
        self.assertEqual(
            FileService.find_name_conflict(self.user, root, "report.pdf"), theirs
        )

    def test_available_file_name_suffixes_taken_names(self):
        FileService.create_file(self.user, "report.pdf")
        FileService.create_file(self.user, "report (Copy).pdf")
        self.assertEqual(
            FileService.available_file_name(self.user, None, "report.pdf"),
            "report (Copy 2).pdf",
        )
        self.assertEqual(
            FileService.available_file_name(self.user, None, "free.pdf"), "free.pdf"
        )


class BlobPathFollowsRowNameTests(TestCase):
    def test_upload_named_differently_from_its_file_keeps_its_own_blob(self):
        user = User.objects.create_user(username="alice", password="pass")
        first = FileService.create_file(
            user, "report.txt", content=ContentFile(b"v1", name="report.txt")
        )
        second = FileService.create_file(
            user, "report (Copy).txt", content=ContentFile(b"v2", name="report.txt")
        )
        self.assertNotEqual(first.content.name, second.content.name)
        with first.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"v1")
        with second.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"v2")


class UploadOnConflictApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.other = User.objects.create_user(username="bob", password="pass")
        self.client.force_authenticate(user=self.user)
        self.existing = FileService.create_file(
            self.user, "report.txt", content=ContentFile(b"v1", name="report.txt")
        )

    def _post(self, data=b"v2", **extra):
        payload = {
            "name": "report.txt",
            "node_type": "file",
            "content": SimpleUploadedFile("report.txt", data),
            **extra,
        }
        return self.client.post("/api/v1/files", payload, format="multipart")

    def test_default_still_rejects_the_collision(self):
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertIn("same name", response.data["name"][0])
        self.assertEqual(File.objects.filter(owner=self.user).count(), 1)

    def test_unknown_value_is_rejected(self):
        response = self._post(on_conflict="explode")
        self.assertEqual(response.status_code, 400)
        self.assertIn("on_conflict", response.data)

    def test_rename_stores_the_upload_under_a_free_name(self):
        response = self._post(on_conflict="rename")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["name"], "report (Copy).txt")
        self.assertNotEqual(response.data["uuid"], str(self.existing.uuid))
        self.existing.refresh_from_db()
        with self.existing.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"v1")

    def test_rename_is_a_noop_without_a_collision(self):
        response = self._post(name="fresh.txt", on_conflict="rename")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["name"], "fresh.txt")

    def test_replace_writes_into_the_existing_file(self):
        response = self._post(on_conflict="replace")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["uuid"], str(self.existing.uuid))
        self.assertEqual(File.objects.filter(owner=self.user).count(), 1)
        self.existing.refresh_from_db()
        self.assertEqual(self.existing.size, 2)
        with self.existing.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"v2")

    def test_replace_creates_when_nothing_collides(self):
        response = self._post(name="fresh.txt", on_conflict="replace")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(File.objects.filter(owner=self.user).count(), 2)

    def test_replace_respects_another_users_lock(self):
        File.objects.filter(pk=self.existing.pk).update(
            locked_by=self.other,
            locked_at=timezone.now(),
            lock_expires_at=timezone.now() + timedelta(minutes=5),
        )
        response = self._post(on_conflict="replace")
        self.assertEqual(response.status_code, 423)
        self.existing.refresh_from_db()
        with self.existing.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"v1")

    def test_replacing_a_teammates_file_notifies_its_owner(self):
        group = Group.objects.create(name="Marketing")
        self.user.groups.add(group)
        self.other.groups.add(group)
        root = FileService.create_folder(self.other, "Marketing Files", group=group)
        theirs = FileService.create_file(
            self.other,
            "plan.txt",
            parent=root,
            content=ContentFile(b"v1", name="plan.txt"),
        )
        response = self.client.post(
            "/api/v1/files",
            {
                "name": "plan.txt",
                "node_type": "file",
                "parent": str(root.uuid),
                "content": SimpleUploadedFile("plan.txt", b"v2"),
                "on_conflict": "replace",
            },
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["uuid"], str(theirs.uuid))
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.other, title__contains="plan.txt"
            ).exists()
        )
        # Replacing your own file is not news to you.
        self._post(on_conflict="replace")
        self.assertFalse(Notification.objects.filter(recipient=self.user).exists())

    def test_replace_without_content_is_rejected(self):
        response = self.client.post(
            "/api/v1/files",
            {"name": "report.txt", "node_type": "file", "on_conflict": "replace"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("content", response.data)

    def test_on_conflict_is_ignored_on_update(self):
        other = FileService.create_file(self.user, "other.txt")
        response = self.client.patch(
            f"/api/v1/files/{other.uuid}",
            {"name": "report.txt", "on_conflict": "rename"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)
