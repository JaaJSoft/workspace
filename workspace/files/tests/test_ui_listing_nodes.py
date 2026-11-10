"""A few nodes of a listing rendered on their own: ``?nodes=`` on its URL.

The page swaps them in place when a push says they changed. Each comes back
exactly as the listing renders it, and only when that listing holds it.
"""

import re
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.ui.views import LISTING_NODES_LIMIT
from workspace.users.services.settings import set_setting

User = get_user_model()


class ListingNodesFixtures:
    def setUp(self):
        super().setUp()
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.docs = FileService.create_folder(self.alice, "Docs")
        self.report = self.file(self.alice, "report.txt", parent=self.docs)
        self.notes = self.file(self.alice, "notes.txt", parent=self.docs)
        self.client.force_login(self.alice)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    @staticmethod
    def file(owner, name, **fields):
        return File.objects.create(
            owner=owner,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            **fields,
        )

    def nodes(self, url, *uuids, **headers):
        separator = "&" if "?" in url else "?"
        return self.client.get(
            f"{url}{separator}nodes={','.join(str(u) for u in uuids)}",
            headers={"X-Alpine-Request": "true", **headers},
        )

    @staticmethod
    def item(html, node, tag="tr"):
        """The whole element the listing renders for *node*, None when absent."""
        match = re.search(
            rf'<{tag}\b[^>]*\bid="node-{node.uuid}".*?</{tag}>', html, re.DOTALL
        )
        return match.group(0) if match else None


class ListingNodesTests(ListingNodesFixtures, TestCase):
    def test_only_the_requested_nodes_come_back(self):
        response = self.nodes(f"/files/{self.docs.uuid}", self.report.uuid)

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIsNotNone(self.item(html, self.report))
        self.assertNotIn(str(self.notes.uuid), html)
        # The nodes alone: none of the page around them.
        self.assertNotIn('id="folder-browser"', html)

    def test_a_row_is_the_one_the_listing_renders(self):
        File.objects.filter(pk=self.report.pk).update(
            processing_status=File.ProcessingStatus.PENDING
        )
        listing = self.client.get(f"/files/{self.docs.uuid}").content.decode()

        response = self.nodes(
            f"/files/{self.docs.uuid}", self.report.uuid, self.notes.uuid
        )

        html = response.content.decode()
        for node in (self.report, self.notes):
            self.assertIsNotNone(self.item(listing, node))
            self.assertEqual(self.item(html, node), self.item(listing, node))

    def test_a_card_is_the_one_the_mosaic_renders(self):
        set_setting(self.alice, "files", "preferences", {"defaultViewMode": "mosaic"})
        listing = self.client.get(f"/files/{self.docs.uuid}").content.decode()

        response = self.nodes(f"/files/{self.docs.uuid}", self.report.uuid)

        html = response.content.decode()
        self.assertNotIn("<tr", html)
        card = re.search(rf'<div\b[^>]*\bid="node-{self.report.uuid}"[^>]*>', html)
        self.assertIsNotNone(card)
        self.assertIn(card.group(0), listing)

    def test_the_root_listing_answers_for_its_own_nodes(self):
        at_root = self.file(self.alice, "root.txt")

        html = self.nodes("/files", at_root.uuid, self.report.uuid).content.decode()

        self.assertIsNotNone(self.item(html, at_root))
        # report.txt is in Docs, not at the root.
        self.assertIsNone(self.item(html, self.report))

    def test_the_trash_answers_for_trashed_nodes(self):
        FileService.soft_delete(self.report, acting_user=self.alice)

        html = self.nodes("/files/trash", self.report.uuid).content.decode()
        elsewhere = self.nodes(
            f"/files/{self.docs.uuid}", self.report.uuid
        ).content.decode()

        self.assertIsNotNone(self.item(html, self.report))
        self.assertIsNone(self.item(elsewhere, self.report))

    def test_a_special_view_keeps_its_scope(self):
        favorites = self.nodes("/files?favorites=1", self.report.uuid)

        self.assertEqual(favorites.status_code, 200)
        self.assertIsNone(self.item(favorites.content.decode(), self.report))

    def test_a_malformed_list_is_refused(self):
        for value in ("", "nope", f"{self.report.uuid},", f"{self.report.uuid},x"):
            with self.subTest(value=value):
                response = self.client.get(
                    f"/files/{self.docs.uuid}",
                    {"nodes": value},
                    headers={"X-Alpine-Request": "true"},
                )
                self.assertEqual(response.status_code, 400)

    def test_a_list_past_the_limit_is_refused(self):
        at_limit = [uuid.uuid4() for _ in range(LISTING_NODES_LIMIT)]

        self.assertEqual(
            self.nodes(f"/files/{self.docs.uuid}", *at_limit).status_code, 200
        )
        self.assertEqual(
            self.nodes(f"/files/{self.docs.uuid}", *at_limit, uuid.uuid4()).status_code,
            400,
        )

    def test_a_page_load_ignores_the_parameter(self):
        response = self.client.get(
            f"/files/{self.docs.uuid}", {"nodes": str(self.report.uuid)}
        )

        html = response.content.decode()
        self.assertIsNotNone(self.item(html, self.report))
        self.assertIsNotNone(self.item(html, self.notes))


class ListingNodesAccessTests(ListingNodesFixtures, TestCase):
    """A node the caller cannot see answers like one that does not exist."""

    def test_a_hidden_node_answers_like_an_absent_one(self):
        secret = self.file(self.bob, "secret.txt")

        hidden = self.nodes("/files", secret.uuid)
        absent = self.nodes("/files", uuid.uuid4())

        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )

    def test_a_hidden_node_drops_out_of_a_batch_like_an_absent_one(self):
        secret = self.file(self.bob, "secret.txt", parent=None)

        hidden = self.nodes(f"/files/{self.docs.uuid}", self.report.uuid, secret.uuid)
        absent = self.nodes(f"/files/{self.docs.uuid}", self.report.uuid, uuid.uuid4())

        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )
        self.assertIsNotNone(self.item(hidden.content.decode(), self.report))

    def test_a_folder_out_of_reach_answers_like_a_missing_one(self):
        private = FileService.create_folder(self.bob, "Private")
        inside = self.file(self.bob, "inside.txt", parent=private)

        hidden = self.nodes(f"/files/{private.uuid}", inside.uuid)
        absent = self.nodes(f"/files/{uuid.uuid4()}", inside.uuid)

        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(absent.status_code, 404)

    def test_a_group_folder_answers_its_members_only(self):
        team = Group.objects.create(name="Team")
        self.bob.groups.add(team)
        root = FileService.create_folder(owner=self.bob, name="Team", group=team)
        shared = File.objects.create(
            owner=self.bob,
            name="plan.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            parent=root,
            group=team,
        )

        outsider = self.nodes(f"/files/{root.uuid}", shared.uuid)
        self.client.force_login(self.bob)
        member = self.nodes(f"/files/{root.uuid}", shared.uuid)

        self.assertEqual(outsider.status_code, 404)
        self.assertIsNotNone(self.item(member.content.decode(), shared))
