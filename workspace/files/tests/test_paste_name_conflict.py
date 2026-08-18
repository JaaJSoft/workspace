"""Copying or moving a file into a folder that already holds one with its name."""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FileService

User = get_user_model()


def _bytes(file_obj):
    file_obj.refresh_from_db()
    with file_obj.content.open("rb") as fh:
        return fh.read()


class AvailableFileNameAvoidingTests(TestCase):
    def test_avoids_names_taken_in_other_folders_too(self):
        user = User.objects.create_user(username="alice", password="pass")
        src = FileService.create_folder(user, "Src")
        dst = FileService.create_folder(user, "Dst")
        FileService.create_file(user, "report.pdf", parent=dst)
        FileService.create_file(user, "report (Copy).pdf", parent=src)
        self.assertEqual(
            FileService.available_file_name(user, dst, "report.pdf", avoiding=(src,)),
            "report (Copy 2).pdf",
        )


class CopyOnConflictApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_authenticate(user=self.user)
        self.src = FileService.create_folder(self.user, "Src")
        self.dst = FileService.create_folder(self.user, "Dst")
        self.source = FileService.create_file(
            self.user,
            "report.txt",
            parent=self.src,
            content=ContentFile(b"source", name="report.txt"),
        )
        self.existing = FileService.create_file(
            self.user,
            "report.txt",
            parent=self.dst,
            content=ContentFile(b"existing", name="report.txt"),
        )

    def _copy(self, parent, **extra):
        return self.client.post(
            f"/api/v1/files/{self.source.uuid}/copy",
            {"parent": str(parent.uuid) if parent else None, **extra},
            format="json",
        )

    def test_default_keeps_renaming(self):
        response = self._copy(self.dst)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["name"], "report (Copy).txt")
        self.assertEqual(_bytes(self.existing), b"existing")

    def test_error_rejects_the_collision(self):
        response = self._copy(self.dst, on_conflict="error")
        self.assertEqual(response.status_code, 400)
        self.assertIn("same name", response.data["name"][0])
        self.assertEqual(File.objects.filter(parent=self.dst).count(), 1)

    def test_replace_writes_into_the_existing_file(self):
        response = self._copy(self.dst, on_conflict="replace")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["uuid"], str(self.existing.uuid))
        self.assertEqual(_bytes(self.existing), b"source")
        self.assertEqual(_bytes(self.source), b"source")
        self.assertNotEqual(self.existing.content.name, self.source.content.name)
        self.assertEqual(File.objects.filter(parent=self.dst).count(), 1)

    def test_replace_without_collision_just_copies(self):
        empty = FileService.create_folder(self.user, "Empty")
        response = self._copy(empty, on_conflict="replace")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.data["name"], "report.txt")

    def test_copy_into_its_own_folder_always_renames(self):
        for mode in ("replace", "error"):
            response = self._copy(self.src, on_conflict=mode)
            self.assertEqual(response.status_code, 201, (mode, response.content))
        names = set(File.objects.filter(parent=self.src).values_list("name", flat=True))
        self.assertEqual(
            names, {"report.txt", "report (Copy).txt", "report (Copy 2).txt"}
        )

    def test_unknown_value_is_rejected(self):
        response = self._copy(self.dst, on_conflict="merge")
        self.assertEqual(response.status_code, 400)
        self.assertIn("on_conflict", response.data)


class MoveOnConflictApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_authenticate(user=self.user)
        self.src = FileService.create_folder(self.user, "Src")
        self.dst = FileService.create_folder(self.user, "Dst")
        self.moved = FileService.create_file(
            self.user,
            "report.txt",
            parent=self.src,
            content=ContentFile(b"moved", name="report.txt"),
        )
        self.existing = FileService.create_file(
            self.user,
            "report.txt",
            parent=self.dst,
            content=ContentFile(b"existing", name="report.txt"),
        )

    def _move(self, **extra):
        return self.client.patch(
            f"/api/v1/files/{self.moved.uuid}",
            {"parent": str(self.dst.uuid), **extra},
            format="json",
        )

    def test_default_rejects_with_the_field_message(self):
        response = self._move()
        self.assertEqual(response.status_code, 400)
        self.assertIn("same name", response.data["name"][0])
        self.moved.refresh_from_db()
        self.assertEqual(self.moved.parent_id, self.src.pk)

    def test_rename_moves_under_a_free_name(self):
        # Taken in the source folder too: the file is renamed before it moves.
        FileService.create_file(self.user, "report (Copy).txt", parent=self.src)
        response = self._move(on_conflict="rename")
        self.assertEqual(response.status_code, 200, response.content)
        self.moved.refresh_from_db()
        self.assertEqual(self.moved.parent_id, self.dst.pk)
        self.assertEqual(self.moved.name, "report (Copy 2).txt")
        self.assertEqual(_bytes(self.moved), b"moved")
        self.assertEqual(_bytes(self.existing), b"existing")

    def test_replace_gives_the_existing_file_the_content_and_trashes_the_moved_one(
        self,
    ):
        response = self._move(on_conflict="replace")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["uuid"], str(self.existing.uuid))
        self.assertEqual(_bytes(self.existing), b"moved")
        self.moved.refresh_from_db()
        self.assertIsNotNone(self.moved.deleted_at)
        self.assertEqual(self.moved.parent_id, self.src.pk)
        # The trashed row keeps its own blob, so it can come back intact.
        self.assertEqual(_bytes(self.moved), b"moved")
        self.assertNotEqual(self.moved.content.name, self.existing.content.name)

    def test_on_conflict_is_ignored_on_a_plain_rename(self):
        other = FileService.create_file(self.user, "other.txt", parent=self.dst)
        response = self.client.patch(
            f"/api/v1/files/{other.uuid}",
            {"name": "report.txt", "on_conflict": "rename"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("name", response.data)
