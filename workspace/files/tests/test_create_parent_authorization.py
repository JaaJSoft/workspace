from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APITestCase

from workspace.files.models import File, FileShare

User = get_user_model()


class CreateParentAuthorizationTests(APITestCase):
    """``POST /api/v1/files`` only creates into a live folder the caller can edit."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.group = Group.objects.create(name="Engineering")
        self.bob.groups.add(self.group)
        self.alice_private = File.objects.create(
            owner=self.alice, name="Private", node_type=File.NodeType.FOLDER
        )
        self.client.force_authenticate(user=self.bob)

    def _create_file(self, parent, name="x.txt"):
        return self.client.post(
            "/api/v1/files",
            {
                "name": name,
                "node_type": "file",
                "parent": str(parent.uuid),
                "content": SimpleUploadedFile(name, b"hello", "text/plain"),
            },
            format="multipart",
        )

    def _create_folder(self, parent, name="Sub"):
        return self.client.post(
            "/api/v1/files",
            {"name": name, "node_type": "folder", "parent": str(parent.uuid)},
            format="json",
        )

    def _assert_refused(self, response, parent):
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("parent", response.json())
        self.assertFalse(File.objects.filter(parent=parent).exists())

    def test_file_in_another_users_folder_is_refused(self):
        self._assert_refused(self._create_file(self.alice_private), self.alice_private)

    def test_folder_in_another_users_folder_is_refused(self):
        self._assert_refused(
            self._create_folder(self.alice_private), self.alice_private
        )

    def test_unreachable_and_missing_parent_answer_alike(self):
        refused = self._create_folder(self.alice_private).json()["parent"]
        self.alice_private.delete()
        missing = self._create_folder(self.alice_private).json()["parent"]
        self.assertEqual(refused, missing)

    def test_read_write_share_does_not_allow_creating_children(self):
        FileShare.objects.create(
            file=self.alice_private,
            shared_by=self.alice,
            shared_with=self.bob,
            permission="rw",
        )
        self._assert_refused(self._create_file(self.alice_private), self.alice_private)
        self._assert_refused(
            self._create_folder(self.alice_private), self.alice_private
        )

    def test_group_folder_of_another_group_is_refused(self):
        other = Group.objects.create(name="Sales")
        alice_group_folder = File.objects.create(
            owner=self.alice,
            name="Sales",
            node_type=File.NodeType.FOLDER,
            group=other,
        )
        self._assert_refused(self._create_file(alice_group_folder), alice_group_folder)

    def test_trashed_own_folder_is_refused(self):
        trashed = File.objects.create(
            owner=self.bob,
            name="Old",
            node_type=File.NodeType.FOLDER,
            deleted_at=timezone.now(),
        )
        self._assert_refused(self._create_file(trashed), trashed)
        self._assert_refused(self._create_folder(trashed), trashed)

    def test_file_as_parent_is_refused(self):
        own_file = File.objects.create(
            owner=self.bob, name="notes.txt", node_type=File.NodeType.FILE
        )
        self._assert_refused(self._create_file(own_file), own_file)
        self._assert_refused(self._create_folder(own_file), own_file)

    def test_own_folder_is_allowed(self):
        mine = File.objects.create(
            owner=self.bob, name="Mine", node_type=File.NodeType.FOLDER
        )
        response = self._create_file(mine)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["path"], "Mine/x.txt")
        response = self._create_folder(mine)
        self.assertEqual(response.status_code, 201, response.content)

    def test_group_folder_of_own_group_is_allowed(self):
        group_folder = File.objects.create(
            owner=self.alice,
            name="Engineering",
            node_type=File.NodeType.FOLDER,
            group=self.group,
        )
        response = self._create_file(group_folder)
        self.assertEqual(response.status_code, 201, response.content)
        created = File.objects.get(uuid=response.json()["uuid"])
        self.assertEqual(created.parent_id, group_folder.pk)
        self.assertEqual(created.group_id, self.group.pk)
        response = self._create_folder(group_folder)
        self.assertEqual(response.status_code, 201, response.content)

    def test_root_is_allowed(self):
        response = self.client.post(
            "/api/v1/files", {"name": "Top", "node_type": "folder"}, format="json"
        )
        self.assertEqual(response.status_code, 201, response.content)


class CreateGroupMatchesParentTests(APITestCase):
    """Under a parent, a node's group is the parent's - never another one."""

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.team = Group.objects.create(name="Team")
        self.other = Group.objects.create(name="Other")
        self.alice.groups.add(self.team, self.other)
        self.team_root = File.objects.create(
            owner=self.alice,
            name="TeamRoot",
            node_type=File.NodeType.FOLDER,
            group=self.team,
        )
        self.mine = File.objects.create(
            owner=self.alice, name="Mine", node_type=File.NodeType.FOLDER
        )
        self.client.force_authenticate(user=self.alice)

    def _create(self, parent, group, node_type="folder"):
        data = {"name": "n", "node_type": node_type, "parent": str(parent.uuid)}
        if group is not None:
            data["group"] = group.pk
        if node_type == "file":
            data["content"] = SimpleUploadedFile("n", b"hello", "text/plain")
            return self.client.post("/api/v1/files", data, format="multipart")
        return self.client.post("/api/v1/files", data, format="json")

    def test_another_group_than_the_parents_is_refused(self):
        for node_type in ("folder", "file"):
            with self.subTest(node_type=node_type):
                response = self._create(self.team_root, self.other, node_type)

                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn("group", response.json())
                self.assertFalse(File.objects.filter(name="n").exists())

    def test_a_group_under_a_personal_parent_is_refused(self):
        response = self._create(self.mine, self.team)

        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(File.objects.filter(name="n").exists())

    def test_the_parents_own_group_is_accepted(self):
        response = self._create(self.team_root, self.team)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(File.objects.get(name="n").group, self.team)

    def test_without_a_parent_the_group_is_kept(self):
        response = self.client.post(
            "/api/v1/files",
            {"name": "Root", "node_type": "folder", "group": self.other.pk},
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(File.objects.get(name="Root").group, self.other)
