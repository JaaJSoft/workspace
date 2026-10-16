"""A group's trash belongs to the group.

Restore and purge used to reach personal nodes only, so a group node nobody
could bring back once trashed - not even the member who deleted it. Any
current member now holds EDIT on the group's trashed nodes, the trash lists
them, and a former member gets none of it.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FilePermission, FileService

User = get_user_model()


class GroupTrashTests(APITestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass123")
        self.bob = User.objects.create_user(username="bob", password="pass123")
        self.carol = User.objects.create_user(username="carol", password="pass123")
        self.team = Group.objects.create(name="Team")
        self.alice.groups.add(self.team)
        self.bob.groups.add(self.team)
        self.root = FileService.create_folder(self.bob, "TeamRoot", group=self.team)
        self.folder = FileService.create_folder(self.bob, "Docs", parent=self.root)
        self.doc = FileService.create_file(
            self.bob,
            "doc.txt",
            parent=self.folder,
            content=ContentFile(b"hello", name="doc.txt"),
        )
        self.loose = FileService.create_file(
            self.bob,
            "loose.txt",
            parent=self.root,
            content=ContentFile(b"loose", name="loose.txt"),
        )
        FileService.soft_delete(self.loose, acting_user=self.bob)
        self.loose.refresh_from_db()
        self.client.force_authenticate(self.alice)

    def tearDown(self):
        cache.clear()

    def _live(self, node):
        node.refresh_from_db()
        return node.deleted_at is None

    def test_a_member_restores_a_node_another_member_trashed(self):
        response = self.client.post(f"/api/v1/files/{self.loose.uuid}/restore")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(self._live(self.loose))
        self.assertEqual(self.loose.parent_id, self.root.pk)

    def test_restoring_below_a_trashed_folder_brings_the_folder_back(self):
        FileService.soft_delete(self.folder, acting_user=self.bob)
        self.doc.refresh_from_db()

        response = self.client.post(f"/api/v1/files/{self.doc.uuid}/restore")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(self._live(self.doc))
        self.assertTrue(self._live(self.folder))
        self.assertEqual(self.doc.parent_id, self.folder.pk)

    def test_a_member_purges_a_node_another_member_trashed(self):
        response = self.client.delete(f"/api/v1/files/{self.loose.uuid}/purge")

        self.assertEqual(response.status_code, 204, response.content)
        self.assertFalse(File.objects.filter(pk=self.loose.pk).exists())

    def test_the_trash_lists_the_groups_trashed_nodes(self):
        api = self.client.get("/api/v1/files/trash")
        trashed = self.client.get("/api/v1/files?trashed=1")
        self.client.force_login(self.alice)
        ui = self.client.get("/files/trash")

        self.assertEqual([n["name"] for n in api.json()], ["loose.txt"])
        self.assertEqual([n["name"] for n in trashed.json()], ["loose.txt"])
        self.assertEqual([n.name for n in ui.context["nodes"]], ["loose.txt"])

    def test_emptying_the_trash_purges_what_it_lists(self):
        response = self.client.delete("/api/v1/files/trash/clean?force=true")

        self.assertEqual(response.json()["deleted"], 1)
        self.assertFalse(File.objects.filter(pk=self.loose.pk).exists())

    def test_the_action_registry_offers_restore_and_purge(self):
        response = self.client.post(
            "/api/v1/files/actions", {"uuids": [str(self.loose.uuid)]}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.content)
        keys = {
            key.split("#")[0] for key in response.json()["files"][str(self.loose.uuid)]
        }
        self.assertTrue({"restore", "purge"} <= keys)

    def test_a_member_previews_it_from_the_trash(self):
        self.client.force_login(self.alice)

        response = self.client.get(f"/files/view/{self.loose.uuid}")

        self.assertEqual(response.status_code, 200)

    def test_trash_rights_stop_at_the_trash(self):
        FileService.soft_delete(self.folder, acting_user=self.bob)
        self.folder.refresh_from_db()

        self.assertEqual(
            FileService.trash_permission(self.alice, self.folder), FilePermission.EDIT
        )
        self.assertIsNone(FileService.get_permission(self.alice, self.folder))
        self.assertEqual(
            FileService.trash_permission(self.alice, self.root), FilePermission.EDIT
        )

    def _assert_locked_out(self, user):
        self.client.force_authenticate(user)
        self.client.force_login(user)

        restore = self.client.post(f"/api/v1/files/{self.loose.uuid}/restore")
        purge = self.client.delete(f"/api/v1/files/{self.loose.uuid}/purge")
        listed = self.client.get("/api/v1/files/trash")
        ui = self.client.get("/files/trash")
        actions = self.client.post(
            "/api/v1/files/actions", {"uuids": [str(self.loose.uuid)]}, format="json"
        )
        preview = self.client.get(f"/files/view/{self.loose.uuid}")

        self.assertEqual(restore.status_code, 404)
        self.assertEqual(purge.status_code, 404)
        self.assertEqual(listed.json(), [])
        self.assertEqual(list(ui.context["nodes"]), [])
        self.assertEqual(actions.status_code, 404)
        self.assertEqual(preview.status_code, 404)
        self.assertFalse(self._live(self.loose))

    def test_a_former_member_gets_none_of_it(self):
        self.alice.groups.remove(self.team)

        self._assert_locked_out(self.alice)

    def test_a_former_member_cannot_restore_what_they_owned(self):
        self.bob.groups.remove(self.team)

        self._assert_locked_out(self.bob)

    def test_an_outsider_gets_none_of_it(self):
        self._assert_locked_out(self.carol)
