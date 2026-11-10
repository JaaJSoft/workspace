from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.files.models import File, FileScan, FileShare, UserStorageQuota
from workspace.files.services import FileService
from workspace.files.services.quota import QuotaExceeded
from workspace.files.services.refusals import Refusal, Refused
from workspace.files.services.shortcuts import create_shortcut
from workspace.files.services.transfers import (
    OnConflict,
    Outcome,
    copy_item,
    move_item,
)

User = get_user_model()


def _bytes(file_obj):
    file_obj.refresh_from_db()
    with file_obj.content.open("rb") as fh:
        return fh.read()


def _file(owner, name, parent, body=b"source"):
    return FileService.create_file(
        owner, name, parent=parent, content=ContentFile(body, name=name)
    )


def _lock(node, holder):
    now = timezone.now()
    File.objects.filter(pk=node.pk).update(
        locked_by=holder,
        locked_at=now,
        lock_expires_at=now + timezone.timedelta(minutes=5),
    )
    node.refresh_from_db()


def _names(parent):
    return sorted(
        File.objects.filter(parent=parent, deleted_at__isnull=True).values_list(
            "name", flat=True
        )
    )


class TransferFixture(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.src = FileService.create_folder(self.alice, "Src")
        self.dst = FileService.create_folder(self.alice, "Dst")
        self.report = _file(self.alice, "report.txt", self.src)

    def assertRefused(self, reason, transfer, *args, **kwargs):
        with self.assertRaises(Refused) as caught:
            transfer(*args, **kwargs)
        self.assertEqual(caught.exception.reason, reason)
        return caught.exception


class CopyItemTests(TransferFixture):
    def test_a_copy_is_a_new_row_with_its_own_blob(self):
        result = copy_item(self.alice, self.report, self.dst)

        self.assertEqual(result.outcome, Outcome.DONE)
        self.assertNotEqual(result.node.pk, self.report.pk)
        self.assertEqual(result.node.parent_id, self.dst.pk)
        self.assertEqual(result.node.name, "report.txt")
        self.assertNotEqual(result.node.content.name, self.report.content.name)
        self.assertEqual(_bytes(result.node), b"source")

    def test_a_copy_to_the_root(self):
        result = copy_item(self.alice, self.report, None)

        self.assertEqual(result.outcome, Outcome.DONE)
        self.assertIsNone(result.node.parent_id)

    def test_a_duplicate_next_to_its_source_is_not_a_rename(self):
        result = copy_item(self.alice, self.report, self.src)

        self.assertEqual(result.outcome, Outcome.DONE)
        self.assertNotEqual(result.node.name, "report.txt")

    def test_a_taken_name_is_renamed_by_default(self):
        _file(self.alice, "report.txt", self.dst, b"existing")

        result = copy_item(self.alice, self.report, self.dst)

        self.assertEqual(result.outcome, Outcome.RENAMED)
        self.assertNotEqual(result.node.name, "report.txt")
        self.assertEqual(len(_names(self.dst)), 2)

    def test_skip_leaves_the_existing_file_alone(self):
        existing = _file(self.alice, "report.txt", self.dst, b"existing")

        result = copy_item(self.alice, self.report, self.dst, OnConflict.SKIP)

        self.assertEqual(result.outcome, Outcome.SKIPPED)
        self.assertIsNone(result.node)
        self.assertEqual(_names(self.dst), ["report.txt"])
        self.assertEqual(_bytes(existing), b"existing")

    def test_skip_copies_when_the_name_is_free(self):
        result = copy_item(self.alice, self.report, self.dst, OnConflict.SKIP)

        self.assertEqual(result.outcome, Outcome.DONE)

    def test_error_refuses_a_taken_name(self):
        _file(self.alice, "report.txt", self.dst, b"existing")

        self.assertRefused(
            Refusal.NAME_TAKEN,
            copy_item,
            self.alice,
            self.report,
            self.dst,
            OnConflict.ERROR,
        )
        self.assertEqual(_names(self.dst), ["report.txt"])

    def test_replace_writes_into_the_existing_row(self):
        existing = _file(self.alice, "report.txt", self.dst, b"existing")

        result = copy_item(self.alice, self.report, self.dst, OnConflict.REPLACE)

        self.assertEqual(result.outcome, Outcome.REPLACED)
        self.assertEqual(result.node.pk, existing.pk)
        self.assertEqual(_bytes(existing), b"source")
        self.assertEqual(_bytes(self.report), b"source")
        self.assertNotEqual(existing.content.name, self.report.content.name)
        self.assertEqual(_names(self.dst), ["report.txt"])

    def test_a_folder_never_replaces_the_one_holding_its_name(self):
        _file(self.alice, "inside.txt", FileService.create_folder(self.alice, "Docs"))
        taken = FileService.create_folder(self.alice, "Docs", parent=self.dst)
        docs = File.objects.get(name="Docs", parent__isnull=True)

        result = copy_item(self.alice, docs, self.dst, OnConflict.REPLACE)

        self.assertEqual(result.outcome, Outcome.RENAMED)
        self.assertNotEqual(result.node.pk, taken.pk)
        self.assertEqual(_names(result.node), ["inside.txt"])
        self.assertEqual(_names(taken), [])

    def test_a_folder_cannot_be_copied_into_itself(self):
        inner = FileService.create_folder(self.alice, "Inner", parent=self.src)

        self.assertRefused(
            Refusal.INTO_ITSELF, copy_item, self.alice, self.src, self.src
        )
        self.assertRefused(Refusal.INTO_ITSELF, copy_item, self.alice, self.src, inner)

    def test_a_folder_out_of_reach_is_no_destination(self):
        theirs = FileService.create_folder(self.bob, "Private")

        self.assertRefused(Refusal.TARGET, copy_item, self.alice, self.report, theirs)
        self.assertEqual(_names(theirs), [])

    def test_a_read_only_folder_is_no_destination(self):
        theirs = FileService.create_folder(self.bob, "Shared")
        FileShare.objects.create(
            file=theirs,
            shared_by=self.bob,
            shared_with=self.alice,
            permission=FileShare.Permission.READ_ONLY,
        )

        self.assertRefused(Refusal.TARGET, copy_item, self.alice, self.report, theirs)

    def test_a_trashed_folder_is_no_destination(self):
        FileService.soft_delete(self.dst, acting_user=self.alice)
        self.dst.refresh_from_db()

        self.assertRefused(Refusal.TARGET, copy_item, self.alice, self.report, self.dst)

    def test_a_file_is_no_destination(self):
        other = _file(self.alice, "other.txt", self.dst)

        self.assertRefused(Refusal.TARGET, copy_item, self.alice, self.report, other)

    def test_a_node_the_user_only_reads_cannot_be_copied(self):
        FileShare.objects.create(
            file=self.report,
            shared_by=self.alice,
            shared_with=self.bob,
            permission=FileShare.Permission.READ_ONLY,
        )

        self.assertRefused(Refusal.NOT_ALLOWED, copy_item, self.bob, self.report, None)
        self.assertFalse(File.objects.filter(owner=self.bob).exists())

    @override_settings(
        FILES_MALWARE_SCAN_ENABLED=True, FILES_MALWARE_ON_DETECTION="block"
    )
    def test_a_quarantined_file_stays_where_it_is(self):
        FileScan.objects.create(
            file=self.report,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at=timezone.now(),
        )

        self.assertRefused(
            Refusal.NOT_ALLOWED, copy_item, self.alice, self.report, self.dst
        )
        self.assertEqual(_names(self.dst), [])

    def test_a_copy_over_quota_is_refused(self):
        UserStorageQuota.objects.create(user=self.alice, quota_bytes=1)

        refusal = self.assertRefused(
            Refusal.QUOTA, copy_item, self.alice, self.report, self.dst
        )
        self.assertEqual(refusal.__cause__.status_code, 413)
        self.assertEqual(_names(self.dst), [])

    def test_replacing_a_teammates_locked_file_names_the_holder(self):
        group = Group.objects.create(name="Design")
        self.alice.groups.add(group)
        self.bob.groups.add(group)
        root = FileService.create_folder(self.bob, "Design", group=group)
        theirs = _file(self.bob, "report.txt", root, b"theirs")
        _lock(theirs, self.bob)

        refusal = self.assertRefused(
            Refusal.LOCKED,
            copy_item,
            self.alice,
            self.report,
            root,
            OnConflict.REPLACE,
        )
        self.assertEqual(refusal.holder, self.bob)
        self.assertEqual(_bytes(theirs), b"theirs")

    def test_a_shortcut_is_copied_as_a_shortcut(self):
        shortcut = create_shortcut(self.alice, self.report, self.src, name="Link")

        result = copy_item(self.alice, shortcut, self.dst)

        self.assertEqual(result.outcome, Outcome.DONE)
        self.assertEqual(result.node.node_type, File.NodeType.SHORTCUT)
        self.assertEqual(result.node.shortcut_target_id, self.report.pk)


class CopyUnderANameTests(TransferFixture):
    def test_a_copy_takes_the_name_asked_for(self):
        result = copy_item(self.alice, self.report, self.dst, name="renamed.txt")

        self.assertEqual(result.outcome, Outcome.DONE)
        self.assertEqual(result.node.name, "renamed.txt")
        self.assertEqual(result.node.mime_type, self.report.mime_type)
        self.assertEqual(_bytes(result.node), b"source")

    def test_a_file_without_content_is_copied_too(self):
        empty = FileService.create_file(self.alice, "empty.txt", parent=self.src)

        result = copy_item(self.alice, empty, self.dst, name="empty copy.txt")

        self.assertEqual(result.node.name, "empty copy.txt")
        self.assertFalse(result.node.content)

    def test_the_name_asked_for_is_refused_when_taken(self):
        _file(self.alice, "taken.txt", self.dst)

        self.assertRefused(
            Refusal.NAME_TAKEN,
            copy_item,
            self.alice,
            self.report,
            self.dst,
            OnConflict.ERROR,
            name="taken.txt",
        )

    def test_a_folder_copy_takes_the_name_asked_for_and_keeps_its_children(self):
        result = copy_item(self.alice, self.src, self.dst, name="Backup")

        self.assertEqual(result.node.name, "Backup")
        self.assertEqual(_names(result.node), ["report.txt"])


class InterruptedCopyTests(IsolatedMediaRootMixin, TransferFixture):
    def _blobs(self):
        return sorted(blob.name for blob in default_storage.iter_blobs("files"))

    def test_a_copy_stopped_halfway_leaves_nothing_behind(self):
        _file(self.alice, "second.txt", self.src)
        _file(self.alice, "third.txt", self.src)
        rows_before = File.objects.count()
        blobs_before = self._blobs()
        seen = []

        class Stop(Exception):
            pass

        def checkpoint(node):
            seen.append(node.name)
            if len(seen) == 2:
                raise Stop

        with self.assertRaises(Stop):
            copy_item(self.alice, self.src, self.dst, checkpoint=checkpoint)

        self.assertEqual(len(seen), 2)
        self.assertEqual(File.objects.count(), rows_before)
        self.assertEqual(self._blobs(), blobs_before)
        self.assertEqual(_names(self.dst), [])

    def test_the_checkpoint_sees_every_copied_node(self):
        _file(self.alice, "second.txt", self.src)
        seen = []

        result = copy_item(
            self.alice, self.src, self.dst, checkpoint=lambda node: seen.append(node.pk)
        )

        copied = set(
            File.objects.filter(path__startswith=result.node.path).values_list(
                "pk", flat=True
            )
        )
        self.assertEqual(set(seen), copied)
        self.assertEqual(len(seen), 3)


class MoveItemTests(TransferFixture):
    def test_a_move_changes_the_parent(self):
        result = move_item(self.alice, self.report, self.dst)

        self.assertEqual(result.outcome, Outcome.DONE)
        self.report.refresh_from_db()
        self.assertEqual(self.report.parent_id, self.dst.pk)
        self.assertEqual(_bytes(self.report), b"source")

    def test_a_move_to_the_root(self):
        result = move_item(self.alice, self.report, None)

        self.assertEqual(result.outcome, Outcome.DONE)
        self.report.refresh_from_db()
        self.assertIsNone(self.report.parent_id)

    def test_a_move_to_where_it_already_is_does_nothing(self):
        result = move_item(self.alice, self.report, self.src)

        self.assertEqual(result.outcome, Outcome.SKIPPED)
        self.assertIsNone(result.node)

    def test_a_taken_name_is_refused_by_default(self):
        _file(self.alice, "report.txt", self.dst, b"existing")

        self.assertRefused(
            Refusal.NAME_TAKEN, move_item, self.alice, self.report, self.dst
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.parent_id, self.src.pk)

    def test_skip_leaves_both_files_alone(self):
        existing = _file(self.alice, "report.txt", self.dst, b"existing")

        result = move_item(self.alice, self.report, self.dst, OnConflict.SKIP)

        self.assertEqual(result.outcome, Outcome.SKIPPED)
        self.report.refresh_from_db()
        self.assertEqual(self.report.parent_id, self.src.pk)
        self.assertEqual(_bytes(existing), b"existing")

    def test_replace_keeps_the_existing_row_and_takes_the_moved_bytes(self):
        existing = _file(self.alice, "report.txt", self.dst, b"existing")
        source_blob = self.report.content.name

        result = move_item(self.alice, self.report, self.dst, OnConflict.REPLACE)

        self.assertEqual(result.outcome, Outcome.REPLACED)
        self.assertEqual(result.node.pk, existing.pk)
        self.assertEqual(_bytes(existing), b"source")
        # Moved, not copied: the moved row is gone and so is its old blob.
        self.assertFalse(File.objects.filter(pk=self.report.pk).exists())
        self.assertFalse(default_storage.exists(source_blob))
        self.assertEqual(_names(self.dst), ["report.txt"])
        self.assertEqual(_names(self.src), [])

    def test_a_move_can_rename_on_the_way(self):
        result = move_item(self.alice, self.report, self.dst, name="final.txt")

        self.assertEqual(result.outcome, Outcome.DONE)
        self.report.refresh_from_db()
        self.assertEqual(
            (self.report.parent_id, self.report.name), (self.dst.pk, "final.txt")
        )
        self.assertEqual(_bytes(self.report), b"source")

    def test_a_move_within_its_folder_is_a_rename(self):
        result = move_item(self.alice, self.report, self.src, name="final.txt")

        self.assertEqual(result.outcome, Outcome.DONE)
        self.assertEqual(_names(self.src), ["final.txt"])

    def test_renaming_and_moving_takes_whichever_order_is_free(self):
        # Renamed first, "draft.txt" would collide in the source folder: the
        # file has to move first.
        _file(self.alice, "draft.txt", self.src, b"sibling")

        move_item(self.alice, self.report, self.dst, name="draft.txt")

        self.assertEqual(_names(self.dst), ["draft.txt"])
        self.assertEqual(_names(self.src), ["draft.txt"])
        self.report.refresh_from_db()
        self.assertEqual(self.report.parent_id, self.dst.pk)
        self.assertEqual(_bytes(self.report), b"source")

    def test_a_rename_on_the_way_follows_the_rename_rules(self):
        with patch(
            "workspace.files.actions.edit.RenameAction.is_available", return_value=False
        ):
            self.assertRefused(
                Refusal.NOT_ALLOWED,
                move_item,
                self.alice,
                self.report,
                self.dst,
                name="final.txt",
            )
        self.report.refresh_from_db()
        self.assertEqual(self.report.parent_id, self.src.pk)

    def test_a_move_refused_by_the_quota_leaves_the_name_alone(self):
        with patch.object(
            FileService, "check_move_allowed", side_effect=QuotaExceeded("full")
        ):
            self.assertRefused(
                Refusal.QUOTA,
                move_item,
                self.alice,
                self.report,
                self.dst,
                name="x.txt",
            )
        self.report.refresh_from_db()
        self.assertEqual(
            (self.report.parent_id, self.report.name), (self.src.pk, "report.txt")
        )

    def test_rename_takes_a_name_free_in_both_folders(self):
        _file(self.alice, "report.txt", self.dst, b"existing")
        _file(self.alice, "report (Copy).txt", self.src, b"other")

        result = move_item(self.alice, self.report, self.dst, OnConflict.RENAME)

        self.assertEqual(result.outcome, Outcome.RENAMED)
        self.report.refresh_from_db()
        self.assertEqual(self.report.parent_id, self.dst.pk)
        self.assertNotIn(self.report.name, ("report.txt", "report (Copy).txt"))
        self.assertEqual(_bytes(self.report), b"source")

    def test_a_folder_does_not_merge_into_the_one_holding_its_name(self):
        docs = FileService.create_folder(self.alice, "Docs", parent=self.src)
        FileService.create_folder(self.alice, "Docs", parent=self.dst)

        for on_conflict in (OnConflict.ERROR, OnConflict.REPLACE):
            with self.subTest(on_conflict=on_conflict):
                self.assertRefused(
                    Refusal.NAME_TAKEN,
                    move_item,
                    self.alice,
                    docs,
                    self.dst,
                    on_conflict,
                )
        docs.refresh_from_db()
        self.assertEqual(docs.parent_id, self.src.pk)

        result = move_item(self.alice, docs, self.dst, OnConflict.RENAME)

        self.assertEqual(result.outcome, Outcome.RENAMED)
        self.assertEqual(_names(self.dst), ["Docs", "Docs (Copy)"])

    def test_skip_covers_a_folder_holding_the_name(self):
        docs = FileService.create_folder(self.alice, "Docs", parent=self.src)
        FileService.create_folder(self.alice, "Docs", parent=self.dst)

        result = move_item(self.alice, docs, self.dst, OnConflict.SKIP)

        self.assertEqual(result.outcome, Outcome.SKIPPED)
        docs.refresh_from_db()
        self.assertEqual(docs.parent_id, self.src.pk)

    def test_a_folder_cannot_move_into_itself(self):
        inner = FileService.create_folder(self.alice, "Inner", parent=self.src)

        self.assertRefused(Refusal.INTO_ITSELF, move_item, self.alice, self.src, inner)

    def test_a_folder_out_of_reach_is_no_destination(self):
        theirs = FileService.create_folder(self.bob, "Private")

        self.assertRefused(Refusal.TARGET, move_item, self.alice, self.report, theirs)
        self.report.refresh_from_db()
        self.assertEqual(self.report.parent_id, self.src.pk)

    def test_a_node_the_user_only_reads_cannot_be_moved(self):
        FileShare.objects.create(
            file=self.report,
            shared_by=self.alice,
            shared_with=self.bob,
            permission=FileShare.Permission.READ_ONLY,
        )

        self.assertRefused(Refusal.NOT_ALLOWED, move_item, self.bob, self.report, None)
        self.report.refresh_from_db()
        self.assertEqual(self.report.parent_id, self.src.pk)

    def test_a_shortcut_moves_but_never_replaces(self):
        shortcut = create_shortcut(self.alice, self.report, self.src, name="notes.txt")
        existing = _file(self.alice, "notes.txt", self.dst, b"existing")

        self.assertRefused(
            Refusal.NAME_TAKEN,
            move_item,
            self.alice,
            shortcut,
            self.dst,
            OnConflict.REPLACE,
        )
        self.assertEqual(_bytes(existing), b"existing")

        result = move_item(self.alice, shortcut, self.dst, OnConflict.RENAME)

        self.assertEqual(result.outcome, Outcome.RENAMED)
        shortcut.refresh_from_db()
        self.assertEqual(shortcut.parent_id, self.dst.pk)
        self.assertNotEqual(shortcut.name, "notes.txt")


class GroupMoveTests(TransferFixture):
    def setUp(self):
        super().setUp()
        self.group = Group.objects.create(name="Design")
        self.alice.groups.add(self.group)
        self.bob.groups.add(self.group)
        self.root = FileService.create_folder(self.bob, "Design", group=self.group)
        self.theirs = _file(self.bob, "brief.txt", self.root, b"theirs")

    def test_the_root_folder_of_a_group_stays_where_it_is(self):
        self.assertRefused(Refusal.NOT_ALLOWED, move_item, self.bob, self.root, None)
        self.assertRefused(Refusal.NOT_ALLOWED, copy_item, self.bob, self.root, None)

    def test_a_file_locked_by_a_teammate_does_not_move(self):
        _lock(self.theirs, self.bob)

        refusal = self.assertRefused(
            Refusal.LOCKED, move_item, self.alice, self.theirs, self.dst
        )
        self.assertEqual(refusal.holder, self.bob)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.parent_id, self.root.pk)

    def test_a_node_leaving_the_group_becomes_the_movers(self):
        result = move_item(self.alice, self.theirs, self.dst)

        self.assertEqual(result.outcome, Outcome.DONE)
        self.theirs.refresh_from_db()
        self.assertEqual(self.theirs.owner_id, self.alice.pk)
        self.assertIsNone(self.theirs.group_id)
        self.assertEqual(_bytes(self.theirs), b"theirs")

    def test_a_node_leaving_the_group_competes_with_the_movers_names(self):
        _file(self.alice, "brief.txt", self.dst, b"mine")

        self.assertRefused(
            Refusal.NAME_TAKEN, move_item, self.alice, self.theirs, self.dst
        )

        result = move_item(self.alice, self.theirs, self.dst, OnConflict.RENAME)

        self.assertEqual(result.outcome, Outcome.RENAMED)
        self.assertEqual(len(_names(self.dst)), 2)

    def test_replacing_a_teammates_file_from_a_move(self):
        mine = _file(self.alice, "brief.txt", self.src, b"mine")

        result = move_item(self.alice, mine, self.root, OnConflict.REPLACE)

        self.assertEqual(result.outcome, Outcome.REPLACED)
        self.assertEqual(result.node.pk, self.theirs.pk)
        self.assertEqual(_bytes(self.theirs), b"mine")
        self.assertFalse(File.objects.filter(pk=mine.pk).exists())
