"""Shortcuts through every entry point: REST, actions, WebDAV, public links, search."""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APITestCase

from workspace.core.module_registry import SearchScope
from workspace.files.models import File, FileShare
from workspace.files.search import search_files
from workspace.files.services import FileService
from workspace.files.services.filetype import get_icon
from workspace.files.services.public_links import resolve_within
from workspace.files.services.search_index import index_file
from workspace.files.services.sharing import create_share_link, share_file
from workspace.files.services.shortcuts import create_shortcut
from workspace.files.webdav.provider import WorkspaceDAVProvider
from workspace.files.webdav.resources import FolderResource, RootCollection

from .test_webdav import _make_environ

User = get_user_model()


class ShortcutApiFixtures:
    def setUp(self):
        super().setUp()
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.team = Group.objects.create(name="Team")
        self.alice.groups.add(self.team)
        self.bob.groups.add(self.team)
        self.team_root = FileService.create_folder(
            owner=self.alice, name="Team", group=self.team
        )
        self.report = FileService.create_file(
            self.alice,
            "report.pdf",
            content=ContentFile(b"%PDF-1.4 report", name="report.pdf"),
            mime_type="application/pdf",
        )
        self.alice_docs = FileService.create_folder(self.alice, "Docs")
        self.bob_inbox = FileService.create_folder(self.bob, "Inbox")

    def _post_shortcut(self, user, target, parent=None, **extra):
        self.client.force_authenticate(user=user)
        body = {
            "name": extra.pop("name", "link"),
            "node_type": "shortcut",
            "shortcut_target": str(getattr(target, "uuid", target)),
            **extra,
        }
        if parent is not None:
            body["parent"] = str(parent.uuid)
        return self.client.post("/api/v1/files", body, format="json")


class CreateShortcutApiTests(ShortcutApiFixtures, APITestCase):
    def test_creation_answers_the_shortcut_with_its_target(self):
        response = self._post_shortcut(
            self.alice, self.report, self.alice_docs, name="Report"
        )

        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data["node_type"], "shortcut")
        self.assertEqual(data["name"], "Report")
        self.assertNotIn("shortcut_target", data)
        self.assertIsNone(data["content_url"])
        self.assertEqual(data["type_icon"], get_icon(self.report.type))
        self.assertEqual(data["shortcut"]["state"], "ok")
        target = data["shortcut"]["target"]
        self.assertEqual(target["uuid"], str(self.report.uuid))
        self.assertEqual(target["name"], "report.pdf")
        self.assertEqual(target["node_type"], "file")
        self.assertEqual(target["path"], "report.pdf")

    def test_a_shared_file_lands_in_the_recipients_folder(self):
        share_file(
            self.report,
            target_user=self.bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.alice,
        )

        response = self._post_shortcut(self.bob, self.report, self.bob_inbox)

        self.assertEqual(response.status_code, 201, response.content)
        target = response.json()["shortcut"]["target"]
        # The folders above a shared node are the owner's to know.
        self.assertIsNone(target["parent"])
        self.assertEqual(target["path"], "report.pdf")

    def test_an_unreachable_target_answers_like_a_missing_one(self):
        unreachable = self._post_shortcut(self.bob, self.report, self.bob_inbox)
        missing_uuid = uuid.uuid4()
        missing = self._post_shortcut(self.bob, missing_uuid, self.bob_inbox)

        self.assertEqual(unreachable.status_code, 400)
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(
            unreachable.json()["shortcut_target"],
            [
                message.replace(str(missing_uuid), str(self.report.uuid))
                for message in missing.json()["shortcut_target"]
            ],
        )
        self.assertFalse(File.objects.filter(parent=self.bob_inbox).exists())

    def test_a_shortcut_needs_a_target(self):
        self.client.force_authenticate(user=self.alice)
        response = self.client.post(
            "/api/v1/files", {"name": "link", "node_type": "shortcut"}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("shortcut_target", response.json())

    def test_a_folder_names_no_target(self):
        self.client.force_authenticate(user=self.alice)
        response = self.client.post(
            "/api/v1/files",
            {
                "name": "Sub",
                "node_type": "folder",
                "shortcut_target": str(self.report.uuid),
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("shortcut_target", response.json())

    def test_a_shortcut_carries_no_content(self):
        self.client.force_authenticate(user=self.alice)
        response = self.client.post(
            "/api/v1/files",
            {
                "name": "link",
                "node_type": "shortcut",
                "shortcut_target": str(self.report.uuid),
                "content": SimpleUploadedFile("link", b"bytes"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("content", response.json())

    def test_a_shortcut_does_not_replace_a_file(self):
        response = self._post_shortcut(
            self.alice, self.report, name="report.pdf", on_conflict="replace"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("on_conflict", response.json())

    def test_a_group_shortcut_goes_in_a_group_folder(self):
        response = self._post_shortcut(self.alice, self.report, group=self.team.pk)

        self.assertEqual(response.status_code, 400)
        self.assertIn("group", response.json())

    def test_a_taken_name_is_refused_unless_renaming_is_asked_for(self):
        taken = self._post_shortcut(self.alice, self.report, name="report.pdf")
        renamed = self._post_shortcut(
            self.alice, self.report, name="report.pdf", on_conflict="rename"
        )

        self.assertEqual(taken.status_code, 400)
        self.assertIn("name", taken.json())
        self.assertEqual(renamed.status_code, 201, renamed.content)
        self.assertEqual(renamed.json()["name"], "report (Copy).pdf")


class ShortcutUpdateApiTests(ShortcutApiFixtures, APITestCase):
    def setUp(self):
        super().setUp()
        self.shortcut = create_shortcut(self.alice, self.report, self.alice_docs)
        self.client.force_authenticate(user=self.alice)
        self.url = f"/api/v1/files/{self.shortcut.uuid}"

    def test_rename_and_move(self):
        response = self.client.patch(
            self.url,
            {"name": "Q3.pdf", "parent": str(self.team_root.uuid)},
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.shortcut.refresh_from_db()
        self.assertEqual(self.shortcut.name, "Q3.pdf")
        self.assertEqual(self.shortcut.group_id, self.team.pk)
        self.assertEqual(self.shortcut.shortcut_target_id, self.report.pk)

    def test_its_target_is_fixed(self):
        response = self.client.patch(
            self.url, {"shortcut_target": str(self.alice_docs.uuid)}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.shortcut.refresh_from_db()
        self.assertEqual(self.shortcut.shortcut_target_id, self.report.pk)

    def test_it_takes_no_content(self):
        response = self.client.patch(
            self.url,
            {"content": SimpleUploadedFile("x", b"bytes")},
            format="multipart",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("content", response.json())

    def test_trash_restore_and_purge(self):
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        restore = self.client.post(f"{self.url}/restore")
        self.assertEqual(restore.status_code, 200, restore.content)
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        purge = self.client.delete(f"{self.url}/purge")
        self.assertEqual(purge.status_code, 204, purge.content)

        self.assertFalse(File.objects.filter(pk=self.shortcut.pk).exists())
        self.assertTrue(File.objects.filter(pk=self.report.pk).exists())

    def test_copy_makes_another_shortcut(self):
        response = self.client.post(
            f"{self.url}/copy", {"parent": str(self.team_root.uuid)}, format="json"
        )

        self.assertEqual(response.status_code, 201, response.content)
        copied = File.objects.get(uuid=response.json()["uuid"])
        self.assertEqual(copied.node_type, File.NodeType.SHORTCUT)
        self.assertEqual(copied.shortcut_target_id, self.report.pk)

    def test_favorite(self):
        response = self.client.post(f"{self.url}/favorite")

        self.assertEqual(response.status_code, 200)


class ShortcutReadApiTests(ShortcutApiFixtures, APITestCase):
    def test_a_viewer_without_access_learns_nothing_of_the_target(self):
        # Alice files a shortcut to her private report in the team folder.
        create_shortcut(self.alice, self.report, self.team_root, name="Quarterly")
        self.client.force_authenticate(user=self.bob)

        response = self.client.get(f"/api/v1/files?parent={self.team_root.uuid}")

        self.assertEqual(response.status_code, 200)
        (row,) = [r for r in response.json() if r["node_type"] == "shortcut"]
        self.assertEqual(row["shortcut"], {"state": "unavailable", "target": None})
        self.assertEqual(row["type_icon"], "link-2-off")
        self.assertNotIn(str(self.report.uuid), response.content.decode())
        self.assertNotIn("report.pdf", response.content.decode())

    def test_a_trashed_target_is_reported_to_its_owner(self):
        create_shortcut(self.alice, self.report, self.alice_docs)
        FileService.soft_delete(self.report, acting_user=self.alice)
        self.client.force_authenticate(user=self.alice)

        response = self.client.get(f"/api/v1/files?parent={self.alice_docs.uuid}")

        (row,) = response.json()
        self.assertEqual(row["shortcut"]["state"], "trashed")
        self.assertEqual(row["shortcut"]["target"]["uuid"], str(self.report.uuid))

    def test_a_listing_resolves_its_shortcuts_in_constant_queries(self):
        self.client.force_authenticate(user=self.bob)
        url = f"/api/v1/files?parent={self.team_root.uuid}"
        targets = [self.report, self.team_root]

        def listing_queries(count):
            for i in range(count):
                create_shortcut(
                    self.alice, targets[i % 2], self.team_root, keep_both=True
                )
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(queries)

        # The first request fills process-wide caches; measure past it.
        listing_queries(0)
        self.assertEqual(listing_queries(2), listing_queries(10))

    def test_content_and_download_refuse_a_shortcut(self):
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)
        self.client.force_authenticate(user=self.alice)

        content = self.client.get(f"/api/v1/files/{shortcut.uuid}/content")
        download = self.client.get(f"/api/v1/files/{shortcut.uuid}/download")

        self.assertEqual(content.status_code, 400)
        self.assertEqual(download.status_code, 400)

    def test_a_shortcut_is_not_shared(self):
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)
        self.client.force_authenticate(user=self.alice)

        share = self.client.post(
            f"/api/v1/files/{shortcut.uuid}/share",
            {"shared_with": self.bob.pk, "permission": "ro"},
            format="json",
        )
        link = self.client.post(
            f"/api/v1/files/{shortcut.uuid}/share-links", {}, format="json"
        )

        self.assertEqual(share.status_code, 400)
        self.assertEqual(link.status_code, 400)
        self.assertFalse(FileShare.objects.filter(file=shortcut).exists())
        self.assertFalse(shortcut.share_links.exists())


class ShortcutActionsTests(ShortcutApiFixtures, APITestCase):
    def _action_ids(self, user, node):
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/api/v1/files/actions", {"uuids": [str(node.uuid)]}, format="json"
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        return {data["actions"][key]["id"] for key in data["files"][str(node.uuid)]}

    def test_a_shortcut_offers_its_own_actions_and_its_targets_opening(self):
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)

        ids = self._action_ids(self.alice, shortcut)

        self.assertTrue(
            {
                "open_target",
                "rename",
                "cut",
                "copy",
                "delete",
                "toggle_favorite",
                "manage_tags",
                "properties",
            }
            <= ids,
            ids,
        )
        self.assertFalse({"view", "open", "download", "share", "copy_link"} & ids)

    def test_open_target_follows_the_viewers_own_access(self):
        shortcut = create_shortcut(self.alice, self.report, self.team_root)

        ids = self._action_ids(self.bob, shortcut)

        self.assertNotIn("open_target", ids)
        # The shortcut itself is the group's like any node in its folder.
        self.assertIn("rename", ids)

    def test_a_folder_target_opens_for_whoever_may_open_the_folder(self):
        shortcut = create_shortcut(self.bob, self.team_root, self.bob_inbox)

        self.assertIn("open_target", self._action_ids(self.bob, shortcut))

    def test_a_trashed_shortcut_offers_the_trash_actions(self):
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)
        FileService.soft_delete(shortcut, acting_user=self.alice)

        ids = self._action_ids(self.alice, shortcut)

        self.assertEqual(ids, {"restore", "purge"})


class ShortcutOutsideTheApiTests(ShortcutApiFixtures, TestCase):
    def test_webdav_does_not_serve_shortcuts(self):
        create_shortcut(self.alice, self.report, None, name="top link")
        create_shortcut(self.alice, self.report, self.alice_docs, name="inner link")
        environ = _make_environ(user=self.alice)
        provider = WorkspaceDAVProvider()

        self.assertIsNone(provider.get_resource_inst("/top link", environ))
        self.assertIsNone(provider.get_resource_inst("/Docs/inner link", environ))
        self.assertNotIn("top link", RootCollection("/", environ).get_member_names())
        docs = provider.get_resource_inst("/Docs", environ)
        self.assertIsInstance(docs, FolderResource)
        self.assertEqual(docs.get_member_names(), [])

    def test_a_public_link_never_reaches_a_shortcut(self):
        shortcut = create_shortcut(
            self.alice, self.report, self.alice_docs, name="inner link"
        )
        link = create_share_link(self.alice_docs, acting_user=self.alice)

        self.assertIsNone(resolve_within(link, str(shortcut.uuid)))
        page = self.client.get(f"/files/shared/{link.token}")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "inner link")

    def test_search_hits_name_nodes_not_shortcuts(self):
        shortcut = create_shortcut(self.alice, self.report, None, name="budget link")
        budget = FileService.create_file(self.alice, "budget.ods")
        index_file(shortcut)
        index_file(budget)

        names = {
            r.name for r in search_files("budget", self.alice, 10, SearchScope.ALL)
        }

        self.assertEqual(names, {"budget.ods"})
