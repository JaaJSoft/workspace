"""A move lands only in a live folder the caller could create in.

The move path checked membership and ownership but not what the target was,
so a trashed folder or a file passed as the new parent (the latter ending in
a 500 from the storage move).
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FileService

User = get_user_model()


class _MoveFixture:
    def _fixture(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.trashed = FileService.create_folder(self.alice, "Trashed")
        FileService.soft_delete(self.trashed, acting_user=self.alice)
        self.trashed.refresh_from_db()
        self.target_file = FileService.create_file(
            self.alice, "t.txt", content=ContentFile(b"t", name="t.txt")
        )
        self.moved = FileService.create_file(
            self.alice, "m.txt", content=ContentFile(b"m", name="m.txt")
        )


class ValidateMoveTargetTests(_MoveFixture, TestCase):
    def setUp(self):
        self._fixture()

    def test_a_trashed_folder_is_refused(self):
        with self.assertRaises(ValueError):
            FileService.validate_move_target(self.moved, self.trashed, user=self.alice)

    def test_a_file_is_refused(self):
        with self.assertRaises(ValueError):
            FileService.validate_move_target(
                self.moved, self.target_file, user=self.alice
            )

    def test_a_former_members_own_group_folder_is_refused(self):
        team = Group.objects.create(name="Team")
        self.alice.groups.add(team)
        root = FileService.create_folder(self.alice, "TeamRoot", group=team)
        self.alice.groups.remove(team)

        with self.assertRaises(ValueError):
            FileService.validate_move_target(self.moved, root, user=self.alice)


class MoveTargetApiTests(_MoveFixture, APITestCase):
    def setUp(self):
        self._fixture()
        self.client.force_authenticate(self.alice)

    def _move(self, parent):
        return self.client.patch(
            f"/api/v1/files/{self.moved.uuid}",
            {"parent": str(parent.uuid)},
            format="json",
        )

    def test_moving_into_a_trashed_folder_is_refused(self):
        response = self._move(self.trashed)

        self.assertEqual(response.status_code, 400, response.content)
        self.moved.refresh_from_db()
        self.assertIsNone(self.moved.parent_id)

    def test_moving_under_a_file_is_refused(self):
        response = self._move(self.target_file)

        self.assertEqual(response.status_code, 400, response.content)
        self.moved.refresh_from_db()
        self.assertIsNone(self.moved.parent_id)
        self.assertFalse(File.objects.filter(parent=self.target_file).exists())
