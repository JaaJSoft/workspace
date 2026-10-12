"""A folder's subtree is its own namespace, never every row sharing its path.

``path`` is only unique within one user's personal tree or one group's tree,
so two folders in different trees can carry the same path.
"""

import io
import zipfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.files.services import FileService
from workspace.files.services.quota import subtree_bytes

User = get_user_model()


def _file(owner, name, parent, data=b"data"):
    return FileService.create_file(
        owner, name, parent=parent, content=ContentFile(data, name=name)
    )


class PersonalTreesSharingAPathTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.alice_docs = FileService.create_folder(self.alice, "Docs")
        self.bob_docs = FileService.create_folder(self.bob, "Docs")
        self.bob_file = _file(self.bob, "x.txt", self.bob_docs)

    def test_trashing_a_folder_leaves_another_users_same_path_alone(self):
        FileService.soft_delete(self.alice_docs, acting_user=self.alice)

        self.bob_file.refresh_from_db()
        self.assertIsNone(self.bob_file.deleted_at)

    def test_renaming_a_folder_leaves_another_users_same_path_alone(self):
        FileService.rename(self.alice_docs, "Papers", acting_user=self.alice)

        self.bob_file.refresh_from_db()
        self.assertEqual(self.bob_file.path, "Docs/x.txt")

    def test_restoring_a_folder_leaves_another_users_trash_alone(self):
        trashed_at = timezone.now()
        self.alice_docs.soft_delete(deleted_at=trashed_at)
        self.bob_docs.soft_delete(deleted_at=trashed_at)

        FileService.restore(self.alice_docs, acting_user=self.alice)

        self.bob_file.refresh_from_db()
        self.assertEqual(self.bob_file.deleted_at, trashed_at)

    def test_subtree_bytes_counts_only_the_folders_own_files(self):
        _file(self.alice, "a.txt", self.alice_docs, b"12")

        self.assertEqual(subtree_bytes(self.alice_docs, include_trashed=True), 2)


class GroupTreeSharingAPathTests(TestCase):
    """A group root folder and a member's personal folder of the same name."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.group = Group.objects.create(name="Team")
        self.alice.groups.add(self.group)
        self.bob.groups.add(self.group)
        self.group_root = FileService.create_folder(
            self.alice, "Team", group=self.group
        )
        self.group_docs = FileService.create_folder(
            self.alice, "Docs", parent=self.group_root
        )
        personal_team = FileService.create_folder(self.alice, "Team")
        personal_docs = FileService.create_folder(
            self.alice, "Docs", parent=personal_team
        )
        self.personal_file = _file(self.alice, "x.txt", personal_docs)

    def test_trashing_a_group_folder_leaves_a_personal_same_path_alone(self):
        FileService.soft_delete(self.group_docs, acting_user=self.alice)

        self.personal_file.refresh_from_db()
        self.assertIsNone(self.personal_file.deleted_at)

    def test_moving_a_group_folder_out_leaves_a_personal_same_path_alone(self):
        target = FileService.create_folder(self.bob, "Inbox")

        FileService.move(self.group_docs, target, acting_user=self.bob)

        self.personal_file.refresh_from_db()
        self.assertEqual(self.personal_file.owner_id, self.alice.id)
        self.assertEqual(self.personal_file.path, "Team/Docs/x.txt")

    def test_moving_a_group_folder_out_hands_every_members_file_over(self):
        bobs_file = _file(self.bob, "b.txt", self.group_docs)
        target = FileService.create_folder(self.alice, "Inbox")

        FileService.move(self.group_docs, target, acting_user=self.alice)

        bobs_file.refresh_from_db()
        self.assertIsNone(bobs_file.group_id)
        self.assertEqual(bobs_file.owner_id, self.alice.id)
        self.assertEqual(bobs_file.path, "Inbox/Docs/b.txt")


class SubtreeReadsTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.group = Group.objects.create(name="Team")
        self.alice.groups.add(self.group)
        self.bob.groups.add(self.group)
        group_root = FileService.create_folder(self.alice, "Team", group=self.group)
        self.group_docs = FileService.create_folder(
            self.alice, "Docs", parent=group_root
        )
        self.bobs_group_file = _file(self.bob, "b.txt", self.group_docs)
        personal_docs = FileService.create_folder(
            self.alice, "Docs", parent=FileService.create_folder(self.alice, "Team")
        )
        self.personal_file = _file(self.alice, "x.txt", personal_docs)
        self.bob_docs = FileService.create_folder(self.bob, "Docs")
        self.client.force_authenticate(self.alice)

    def test_bulk_download_of_a_group_folder_zips_that_folder_only(self):
        response = self.client.post(
            "/api/v1/files/bulk-download",
            {"uuids": [str(self.group_docs.uuid)]},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        archive = io.BytesIO(b"".join(response.streaming_content))
        with zipfile.ZipFile(archive) as zf:
            self.assertEqual(zf.namelist(), ["Docs/b.txt"])

    def test_descendants_of_another_users_folder_list_none_of_mine(self):
        alices_docs = FileService.create_folder(self.alice, "Docs")
        _file(self.alice, "mine.txt", alices_docs)

        response = self.client.get(
            "/api/v1/files",
            {"parent": str(self.bob_docs.uuid), "descendants": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])
