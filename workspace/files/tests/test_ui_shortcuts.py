"""Shortcuts in the server-rendered browser: rows, order, counts, properties."""

import re

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from workspace.files.actions import ActionRegistry
from workspace.files.ai_tools import FilesToolProvider
from workspace.files.models import File, FileShare
from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.files.services.shortcuts import create_shortcut

User = get_user_model()


class ShortcutBrowserFixtures:
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
            "report.txt",
            content=ContentFile(b"quarterly", name="report.txt"),
            mime_type="text/plain",
        )
        self.docs = FileService.create_folder(self.alice, "Docs")

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def listing(self, user, folder=None):
        self.client.force_login(user)
        url = f"/files/{folder.uuid}" if folder else "/files"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    @staticmethod
    def row(html, name):
        """The opening tag of the listing row whose display name is *name*."""
        match = re.search(rf'<tr\b[^>]*data-display-name="{re.escape(name)}"[^>]*>', html)
        assert match, f"no row for {name}"
        return match.group(0)


class ShortcutRowTests(ShortcutBrowserFixtures, TestCase):
    def test_a_file_shortcut_row_carries_its_target_for_the_viewer(self):
        create_shortcut(self.alice, self.report, self.docs, name="Report link")

        row = self.row(self.listing(self.alice, self.docs), "Report link")

        self.assertIn('data-node-type="shortcut"', row)
        self.assertIn('data-opens="viewer"', row)
        self.assertIn(f'data-target-uuid="{self.report.uuid}"', row)
        # The viewer steps through rows of its own: never the shortcut's uuid.
        self.assertIn('data-viewable="0"', row)

    def test_a_folder_shortcut_links_to_the_target_folder(self):
        create_shortcut(self.alice, self.team_root, self.docs, name="Team link")

        html = self.listing(self.alice, self.docs)

        self.assertIn('data-opens="folder"', self.row(html, "Team link"))
        self.assertIn(f'href="/files/{self.team_root.uuid}"', html)

    def test_a_shortcut_out_of_reach_shows_nothing_of_its_target(self):
        # Alice files a shortcut to her private report in the team folder.
        create_shortcut(self.alice, self.report, self.team_root, name="Quarterly")

        html = self.listing(self.bob, self.team_root)

        row = self.row(html, "Quarterly")
        self.assertNotIn("data-opens", row)
        self.assertNotIn(str(self.report.uuid), html)
        self.assertNotIn("report.txt", html)
        self.assertIn("The original is no longer available.", html)

    def test_a_trashed_target_is_named_as_such_and_does_not_open(self):
        create_shortcut(self.alice, self.report, self.docs, name="Report link")
        FileService.soft_delete(self.report, acting_user=self.alice)

        html = self.listing(self.alice, self.docs)

        self.assertNotIn("data-opens", self.row(html, "Report link"))
        self.assertIn("The original is in the trash.", html)

    def test_a_shared_file_can_sit_in_the_recipients_own_folder(self):
        share_file(
            self.report,
            target_user=self.bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.alice,
        )
        inbox = FileService.create_folder(self.bob, "Inbox")
        create_shortcut(self.bob, self.report, inbox)

        html = self.listing(self.bob, inbox)

        row = self.row(html, "report.txt")
        self.assertIn('data-opens="viewer"', row)
        # The owner's folders stay the owner's: the path is the name alone.
        self.assertIn("Shortcut to /report.txt", html)

    def test_folders_come_first_and_shortcuts_sort_among_the_files(self):
        FileService.create_file(self.alice, "a.txt", parent=self.docs)
        FileService.create_file(self.alice, "c.txt", parent=self.docs)
        FileService.create_folder(self.alice, "Zeta", parent=self.docs)
        create_shortcut(self.alice, self.team_root, self.docs, name="b link")

        html = self.listing(self.alice, self.docs)

        names = re.findall(r'<tr\b[^>]*data-display-name="([^"]+)"', html)
        self.assertEqual(names, ["Zeta", "a.txt", "b link", "c.txt"])

    def test_the_footer_counts_shortcuts_on_their_own(self):
        FileService.create_file(self.alice, "a.txt", parent=self.docs)
        create_shortcut(self.alice, self.team_root, self.docs, name="Team link")
        create_shortcut(self.alice, self.report, self.docs, name="Report link")

        html = self.listing(self.alice, self.docs)

        self.assertRegex(html, r"2\s+shortcuts")
        self.assertRegex(html, r"1\s+file\b")
        self.assertNotRegex(html, r"\d+\s+folders?\b")

    def test_a_listing_resolves_its_shortcuts_in_constant_queries(self):
        self.client.force_login(self.bob)
        listed = FileService.create_folder(self.alice, "Links", parent=self.team_root)
        url = f"/files/{listed.uuid}"
        batches = iter(range(100))

        def page_queries(count):
            batch = next(batches)
            for i in range(count):
                name = f"{batch}-{i}.txt"
                if i % 2:
                    target = FileService.create_file(self.alice, name)
                else:
                    target = FileService.create_file(
                        self.alice, name, parent=self.team_root
                    )
                create_shortcut(self.alice, target, listed)
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(queries)

        # The first request fills process-wide caches; measure past it.
        page_queries(0)
        self.assertEqual(page_queries(2), page_queries(8))


class ShortcutPropertiesTests(ShortcutBrowserFixtures, TestCase):
    def properties(self, user, node):
        self.client.force_login(user)
        response = self.client.get(f"/files/properties/{node.uuid}")
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_panel_names_what_the_shortcut_opens(self):
        shortcut = create_shortcut(self.alice, self.report, self.docs, name="Link")

        html = self.properties(self.alice, shortcut)

        self.assertIn("data-shortcut-target", html)
        self.assertIn("/report.txt", html)
        self.assertIn("window.fileActions.openShortcutTarget", html)

    def test_the_panel_offers_neither_links_nor_comments_on_a_shortcut(self):
        shortcut = create_shortcut(self.alice, self.report, self.docs, name="Link")

        html = self.properties(self.alice, shortcut)

        self.assertNotIn("Public links", html)
        self.assertNotIn("comments", html.lower().split("activity")[0])

    def test_an_unreachable_target_is_not_named(self):
        shortcut = create_shortcut(
            self.alice, self.report, self.team_root, name="Quarterly"
        )

        html = self.properties(self.bob, shortcut)

        self.assertIn("The original is no longer available.", html)
        self.assertNotIn("report.txt", html)
        self.assertNotIn("openShortcutTarget", html)


class CreateShortcutActionTests(ShortcutBrowserFixtures, TestCase):
    def offered(self, user, node):
        return ActionRegistry.is_action_available(
            "create_shortcut",
            user,
            node,
            permission=FileService.get_permission(user, node),
        )

    def test_offered_on_whatever_the_user_can_open(self):
        share_file(
            self.report,
            target_user=self.bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.alice,
        )

        self.assertTrue(self.offered(self.alice, self.report))
        self.assertTrue(self.offered(self.alice, self.docs))
        self.assertTrue(self.offered(self.bob, self.report))
        self.assertTrue(self.offered(self.bob, self.team_root))

    def test_not_offered_on_a_trashed_node_a_shortcut_or_out_of_reach(self):
        shortcut = create_shortcut(self.alice, self.report, self.docs, name="Link")
        trashed = FileService.create_file(self.alice, "old.txt")
        FileService.soft_delete(trashed, acting_user=self.alice)
        trashed.refresh_from_db()

        self.assertFalse(self.offered(self.alice, trashed))
        self.assertFalse(self.offered(self.alice, shortcut))
        self.assertFalse(self.offered(self.bob, self.report))


class ShortcutAssistantSearchTests(ShortcutBrowserFixtures, TestCase):
    def test_the_assistant_finds_targets_not_shortcuts(self):
        create_shortcut(self.alice, self.report, self.docs, name="report link")
        provider = FilesToolProvider()

        class Args:
            query = "report"
            file_type = ""

        result = provider.search_filenames(Args(), self.alice, None, None, {})

        self.assertIn("report.txt", result)
        self.assertNotIn("report link", result)
