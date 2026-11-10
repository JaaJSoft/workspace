"""Shortcuts: creation rules, resolution per viewer, and their life in the tree."""

import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.files.models import File, FileEvent, FileShare
from workspace.files.queries import get_permission
from workspace.files.services import FileService
from workspace.files.services._names import NameTaken
from workspace.files.services.files import GroupMismatch
from workspace.files.services.sharing import share_file, unshare_file
from workspace.files.services.shortcuts import (
    ShortcutState,
    TargetUnreachable,
    create_shortcut,
    resolve_shortcuts,
)
from workspace.files.sync import FileSyncService

User = get_user_model()


class ShortcutFixtures:
    def setUp(self):
        super().setUp()
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.carol = User.objects.create_user(username="carol", password="x")
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
        )
        self.alice_docs = FileService.create_folder(self.alice, "Docs")
        self.bob_inbox = FileService.create_folder(self.bob, "Inbox")


class CreateShortcutTests(ShortcutFixtures, TestCase):
    def test_creates_a_contentless_node_named_after_its_target(self):
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)

        shortcut.refresh_from_db()
        self.assertEqual(shortcut.node_type, File.NodeType.SHORTCUT)
        self.assertEqual(shortcut.name, "report.pdf")
        self.assertEqual(shortcut.shortcut_target_id, self.report.pk)
        self.assertEqual(shortcut.parent_id, self.alice_docs.pk)
        self.assertEqual(shortcut.owner_id, self.alice.pk)
        self.assertFalse(shortcut.content)
        self.assertIsNone(shortcut.size)
        self.assertTrue(
            FileEvent.objects.filter(
                file=shortcut, action=FileEvent.Action.CREATED
            ).exists()
        )

    def test_shortcut_in_a_group_folder_belongs_to_the_group(self):
        shortcut = create_shortcut(self.bob, self.team_root, self.team_root, name="Up")

        self.assertEqual(shortcut.group_id, self.team.pk)
        self.assertEqual(shortcut.owner_id, self.bob.pk)

    def test_a_target_the_owner_cannot_open_is_refused(self):
        with self.assertRaises(TargetUnreachable):
            create_shortcut(self.bob, self.report, self.bob_inbox)
        self.assertFalse(File.objects.filter(node_type=File.NodeType.SHORTCUT).exists())

    def test_a_trashed_target_is_refused(self):
        FileService.soft_delete(self.report, acting_user=self.alice)

        with self.assertRaises(TargetUnreachable):
            create_shortcut(self.alice, self.report, self.alice_docs)

    def test_a_shared_file_can_be_filed_in_the_recipients_own_folder(self):
        share_file(
            self.report,
            target_user=self.bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.alice,
        )

        shortcut = create_shortcut(self.bob, self.report, self.bob_inbox)

        self.assertEqual(shortcut.owner_id, self.bob.pk)
        self.assertEqual(shortcut.shortcut_target_id, self.report.pk)

    def test_a_shortcut_to_a_shortcut_points_at_the_final_target(self):
        first = create_shortcut(
            self.alice, self.report, self.alice_docs, name="Report link"
        )

        second = create_shortcut(self.alice, first, None)

        self.assertEqual(second.shortcut_target_id, self.report.pk)
        self.assertEqual(second.name, "Report link")

    def test_a_taken_name_is_refused_unless_both_are_kept(self):
        # The report itself holds its name at the root.
        with self.assertRaises(NameTaken):
            create_shortcut(self.alice, self.report, None)
        kept = create_shortcut(self.alice, self.report, None, keep_both=True)

        self.assertEqual(kept.name, "report (Copy).pdf")

    def test_a_group_shortcut_at_the_root_is_refused(self):
        with self.assertRaises(GroupMismatch):
            create_shortcut(self.alice, self.report, None, group=self.team)


class ShortcutConstraintTests(ShortcutFixtures, TestCase):
    def test_a_shortcut_needs_a_target(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            File.objects.create(
                owner=self.alice, name="dangling", node_type=File.NodeType.SHORTCUT
            )

    def test_a_file_has_no_target(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            File.objects.create(
                owner=self.alice,
                name="odd.txt",
                node_type=File.NodeType.FILE,
                shortcut_target=self.report,
            )

    def test_a_shortcut_has_no_content(self):
        shortcut = create_shortcut(self.alice, self.report, None, name="link")

        with self.assertRaises(IntegrityError), transaction.atomic():
            File.objects.filter(pk=shortcut.pk).update(content="files/users/x")


class ResolveShortcutsTests(ShortcutFixtures, TestCase):
    def _resolve(self, user, shortcut):
        return resolve_shortcuts(user, [shortcut])[shortcut.pk]

    def test_its_owner_reaches_their_own_target(self):
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)

        resolution = self._resolve(self.alice, shortcut)

        self.assertEqual(resolution.state, ShortcutState.OK)
        self.assertEqual(resolution.target.pk, self.report.pk)

    def test_a_shortcut_grants_no_access_to_its_target(self):
        # Alice files a shortcut to her private report in the team folder:
        # Bob sees the shortcut, and nothing of what it points at.
        shortcut = create_shortcut(self.alice, self.report, self.team_root)

        resolution = self._resolve(self.bob, shortcut)

        self.assertEqual(resolution.state, ShortcutState.UNAVAILABLE)
        self.assertIsNone(resolution.target)
        self.assertIsNone(resolution.permission)
        self.assertIsNone(get_permission(self.bob, self.report))

    def test_access_follows_the_share(self):
        share_file(
            self.report,
            target_user=self.bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.alice,
        )
        shortcut = create_shortcut(self.bob, self.report, self.bob_inbox)
        self.assertEqual(self._resolve(self.bob, shortcut).state, ShortcutState.OK)

        unshare_file(self.report, target_user=self.bob, acting_user=self.alice)
        self.assertEqual(
            self._resolve(self.bob, shortcut).state, ShortcutState.UNAVAILABLE
        )

        share_file(
            self.report,
            target_user=self.bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.alice,
        )
        self.assertEqual(self._resolve(self.bob, shortcut).state, ShortcutState.OK)

    def test_a_trashed_target_is_reported_to_whoever_can_restore_it(self):
        share_file(
            self.report,
            target_user=self.bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.alice,
        )
        own = create_shortcut(self.alice, self.report, self.alice_docs)
        received = create_shortcut(self.bob, self.report, self.bob_inbox)

        FileService.soft_delete(self.report, acting_user=self.alice)

        self.assertEqual(self._resolve(self.alice, own).state, ShortcutState.TRASHED)
        self.assertEqual(
            self._resolve(self.bob, received).state, ShortcutState.UNAVAILABLE
        )

        FileService.restore(self.report, acting_user=self.alice)
        self.assertEqual(self._resolve(self.bob, received).state, ShortcutState.OK)

    def test_a_batch_resolves_in_a_bounded_number_of_queries(self):
        # A target of its own per shortcut, across every permission path: a
        # group node, a personal one Bob could only reach through a share,
        # and a trashed group node, which the trash rule answers.
        batches = iter(range(100))

        def resolve_count(count):
            batch = next(batches)
            shortcuts = []
            for i in range(count):
                name = f"{batch}-{i}"
                if i % 3 == 1:
                    target = FileService.create_file(self.alice, f"{name}.txt")
                else:
                    target = FileService.create_file(
                        self.alice, f"{name}.txt", parent=self.team_root
                    )
                shortcuts.append(create_shortcut(self.alice, target, self.alice_docs))
                if i % 3 == 2:
                    FileService.soft_delete(target, acting_user=self.alice)
            with CaptureQueriesContext(connection) as queries:
                resolve_shortcuts(self.bob, shortcuts)
            return len(queries)

        # The first resolution fills process-wide caches; measure past it.
        resolve_count(3)
        self.assertEqual(resolve_count(3), resolve_count(12))

    def test_other_nodes_are_left_out(self):
        self.assertEqual(resolve_shortcuts(self.alice, [self.report]), {})


class ShortcutModelValidationTests(ShortcutFixtures, TestCase):
    """What a form (the admin) checks before writing a shortcut."""

    def _shortcut(self, target):
        return File(
            owner=self.alice,
            name="link",
            node_type=File.NodeType.SHORTCUT,
            shortcut_target=target,
        )

    def test_a_shortcut_to_a_file_or_folder_is_valid(self):
        self._shortcut(self.report).full_clean()
        self._shortcut(self.alice_docs).full_clean()

    def test_a_shortcut_to_a_shortcut_is_refused(self):
        first = create_shortcut(self.alice, self.report, None, name="first")

        with self.assertRaises(ValidationError) as caught:
            self._shortcut(first).full_clean()
        self.assertIn("shortcut_target", caught.exception.message_dict)

    def test_a_shortcut_to_itself_is_refused(self):
        shortcut = create_shortcut(self.alice, self.report, None, name="loop")
        shortcut.shortcut_target = shortcut

        with self.assertRaises(ValidationError) as caught:
            shortcut.full_clean()
        self.assertIn("shortcut_target", caught.exception.message_dict)


class ShortcutAdminTests(ShortcutFixtures, TestCase):
    def test_the_change_page_does_not_list_every_node_as_a_target(self):
        root = User.objects.create_superuser(username="root", password="x")
        unrelated = FileService.create_file(self.bob, "unrelated.txt")
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)
        self.client.force_login(root)

        response = self.client.get(
            reverse("admin:files_file_change", args=[shortcut.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, str(unrelated.pk))


class ShortcutTreeTests(ShortcutFixtures, TestCase):
    def test_purging_the_target_purges_its_shortcuts_in_every_tree(self):
        share_file(
            self.report,
            target_user=self.bob,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.alice,
        )
        own = create_shortcut(self.alice, self.report, self.alice_docs)
        received = create_shortcut(self.bob, self.report, self.bob_inbox)

        FileService.hard_delete(self.report)

        self.assertFalse(File.objects.filter(pk__in=[own.pk, received.pk]).exists())

    def test_trashing_and_restoring_a_shortcut_leaves_its_target_alone(self):
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)

        FileService.soft_delete(shortcut, acting_user=self.alice)
        shortcut.refresh_from_db()
        self.report.refresh_from_db()
        self.assertIsNotNone(shortcut.deleted_at)
        self.assertIsNone(self.report.deleted_at)

        FileService.restore(shortcut, acting_user=self.alice)
        shortcut.refresh_from_db()
        self.assertIsNone(shortcut.deleted_at)

    def test_rename_and_move_touch_the_shortcut_only(self):
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)

        FileService.rename(shortcut, "Q3.pdf", acting_user=self.alice)
        FileService.move(shortcut, None, acting_user=self.alice)

        shortcut.refresh_from_db()
        self.report.refresh_from_db()
        self.assertEqual((shortcut.name, shortcut.parent_id), ("Q3.pdf", None))
        self.assertEqual(self.report.name, "report.pdf")
        self.assertEqual(shortcut.shortcut_target_id, self.report.pk)

    def test_a_copied_shortcut_points_at_the_same_target(self):
        shortcut = create_shortcut(self.alice, self.report, self.alice_docs)

        copied = FileService.copy(shortcut, None, self.alice, acting_user=self.alice)

        copied.refresh_from_db()
        self.assertEqual(copied.node_type, File.NodeType.SHORTCUT)
        self.assertEqual(copied.shortcut_target_id, self.report.pk)
        self.assertFalse(copied.content)

    def test_a_copied_folder_carries_its_shortcuts(self):
        create_shortcut(self.alice, self.report, self.alice_docs)

        copied = FileService.copy(
            self.alice_docs, self.team_root, self.alice, acting_user=self.alice
        )

        child = File.objects.get(parent=copied)
        self.assertEqual(child.node_type, File.NodeType.SHORTCUT)
        self.assertEqual(child.shortcut_target_id, self.report.pk)
        self.assertEqual(child.group_id, self.team.pk)


class ShortcutStorageTests(IsolatedMediaRootMixin, ShortcutFixtures, TestCase):
    """A shortcut lives in the database alone; the storage walk must agree."""

    def _user_dir(self, *parts):
        return os.path.join(settings.MEDIA_ROOT, "files", "users", "alice", *parts)

    def test_the_disk_sync_keeps_shortcuts(self):
        top = create_shortcut(self.alice, self.report, None, name="top link")
        nested = create_shortcut(self.alice, self.report, self.alice_docs)

        FileSyncService().sync_user_recursive(self.alice)
        FileSyncService().sync_folder_shallow(self.alice, self.alice_docs)

        live = File.objects.filter(
            pk__in=[top.pk, nested.pk], deleted_at__isnull=True
        ).count()
        self.assertEqual(live, 2)

    def test_purging_a_shortcut_leaves_the_directory_at_its_path(self):
        # A directory nobody registered yet, under the name a shortcut holds.
        stray = self._user_dir("Archive")
        os.makedirs(stray)
        with open(os.path.join(stray, "keep.txt"), "wb") as handle:
            handle.write(b"keep")
        shortcut = create_shortcut(self.alice, self.alice_docs, None, name="Archive")

        FileService.hard_delete(shortcut)

        self.assertTrue(os.path.exists(os.path.join(stray, "keep.txt")))
