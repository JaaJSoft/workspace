"""A group's root folder stays where it is: no member moves or trashes it.

Moving it out re-owned the whole subtree to the mover and dropped the group;
trashing it trashed every member's files. The action menu already hid both,
but neither the REST nor the WebDAV write path asked.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.tests import test_webdav

User = get_user_model()
DAV = "http://testserver/dav"


class _GroupRootFixture:
    def _fixture(self):
        self.alice = User.objects.create_user(username="alice", password="pass123")
        self.bob = User.objects.create_user(username="bob", password="pass123")
        self.team = Group.objects.create(name="Team")
        self.alice.groups.add(self.team)
        self.bob.groups.add(self.team)
        self.root = FileService.create_folder(self.bob, "TeamRoot", group=self.team)
        self.bob_file = FileService.create_file(
            self.bob,
            "bob.txt",
            parent=self.root,
            content=ContentFile(b"bob", name="bob.txt"),
        )
        self.mine = FileService.create_folder(self.alice, "Mine")

    def _assert_untouched(self):
        self.root.refresh_from_db()
        self.bob_file.refresh_from_db()
        self.assertIsNone(self.root.parent_id)
        self.assertIsNone(self.root.deleted_at)
        self.assertEqual(self.root.group, self.team)
        self.assertEqual(self.bob_file.owner, self.bob)
        self.assertEqual(self.bob_file.group, self.team)
        self.assertIsNone(self.bob_file.deleted_at)


class GroupRootRestTests(_GroupRootFixture, APITestCase):
    def setUp(self):
        self._fixture()
        self.client.force_authenticate(self.alice)

    def tearDown(self):
        cache.clear()

    def test_a_member_cannot_move_the_group_root(self):
        response = self.client.patch(
            f"/api/v1/files/{self.root.uuid}",
            {"parent": str(self.mine.uuid)},
            format="json",
        )

        self.assertEqual(response.status_code, 400, response.content)
        self._assert_untouched()

    def test_a_member_cannot_trash_the_group_root(self):
        response = self.client.delete(f"/api/v1/files/{self.root.uuid}")

        self.assertEqual(response.status_code, 403, response.content)
        self._assert_untouched()

    def test_a_member_cannot_copy_the_group_root(self):
        response = self.client.post(
            f"/api/v1/files/{self.root.uuid}/copy",
            {"parent": str(self.mine.uuid)},
            format="json",
        )

        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(File.objects.filter(parent=self.mine).exists())

    def test_a_folder_inside_the_group_root_still_moves_and_trashes(self):
        sub = FileService.create_folder(self.alice, "Sub", parent=self.root)
        other = FileService.create_folder(self.alice, "Other", parent=self.root)

        moved = self.client.patch(
            f"/api/v1/files/{sub.uuid}", {"parent": str(other.uuid)}, format="json"
        )
        trashed = self.client.delete(f"/api/v1/files/{other.uuid}")

        self.assertEqual(moved.status_code, 200, moved.content)
        self.assertEqual(trashed.status_code, 204, trashed.content)


class GroupRootServiceTests(_GroupRootFixture, TestCase):
    def setUp(self):
        self._fixture()

    def test_move_refuses_the_group_root(self):
        with self.assertRaises(ValueError):
            FileService.move(self.root, self.mine, acting_user=self.alice)
        self._assert_untouched()

    def test_soft_delete_refuses_the_group_root(self):
        with self.assertRaises(ValueError):
            FileService.soft_delete(self.root, acting_user=self.alice)
        self._assert_untouched()

    def test_rename_refuses_the_group_root(self):
        with self.assertRaises(ValueError):
            FileService.rename(self.root, "Renamed", acting_user=self.alice)
        self.root.refresh_from_db()
        self.assertEqual(self.root.name, "TeamRoot")
        self._assert_untouched()


class GroupRootWebDAVTests(_GroupRootFixture, TestCase):
    _request = test_webdav.WebDAVIntegrationTests._request

    def setUp(self):
        from workspace.files.webdav.app import create_webdav_app

        self._app = create_webdav_app()
        self._fixture()
        self.auth = test_webdav._basic_auth_header("alice", "pass123")

    def test_a_member_cannot_move_the_group_root(self):
        code, _, _ = self._request(
            "MOVE", "/TeamRoot", headers={"Destination": f"{DAV}/Mine/TeamRoot"}
        )

        self.assertEqual(code, 403)
        self._assert_untouched()

    def test_a_member_cannot_delete_the_group_root(self):
        code, _, _ = self._request("DELETE", "/TeamRoot")

        self.assertEqual(code, 403)
        self._assert_untouched()

    def test_a_member_cannot_rename_the_group_root(self):
        code, _, _ = self._request(
            "MOVE", "/TeamRoot", headers={"Destination": f"{DAV}/Renamed"}
        )

        self.assertEqual(code, 403)
        self.root.refresh_from_db()
        self.assertEqual(self.root.name, "TeamRoot")
        self._assert_untouched()
