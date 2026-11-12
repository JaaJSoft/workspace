"""The per-bucket counter equals the aggregate after every write path."""

import io
import zipfile

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File, StorageUsage
from workspace.files.services import bucket_usage, quota, trash, tree
from workspace.files.services.content_writes import (
    clear_content,
    move_content_from,
    update_content,
)
from workspace.files.services.extract import extract_zip
from workspace.files.services.hard_delete import (
    hard_delete_in_batches,
    hard_delete_tree,
)

User = get_user_model()


class CounterMatchesAggregateTests(TestCase):
    """Each test runs one write path and checks every bucket afterwards.

    The buckets are seeded before the write, so the counter has to be moved
    by the write itself rather than computed fresh on the next read.
    """

    def setUp(self):
        self.user = User.objects.create_user(username="usage", password="pw")
        self.other = User.objects.create_user(username="other", password="pw")
        self.group = Group.objects.create(name="Design")
        self.second_group = Group.objects.create(name="Ops")
        for group in (self.group, self.second_group):
            self.user.groups.add(group)
            self.other.groups.add(group)
        self.group_root = tree.create_folder(self.user, "Design", group=self.group)
        self.ops_root = tree.create_folder(self.user, "Ops", group=self.second_group)
        self.buckets = [
            (self.user, None),
            (self.other, None),
            (self.user, self.group),
            (self.user, self.second_group),
        ]
        for owner, group in self.buckets:
            bucket_usage.used_bytes(owner=owner, group=group)

    def _upload(self, name, payload, parent=None, owner=None):
        return tree.create_file(
            owner or self.user,
            name,
            parent=parent,
            content=ContentFile(payload, name=name),
        )

    def assertCountersMatch(self):
        for owner, group in self.buckets:
            expected = (
                quota.group_usage(group)
                if group is not None
                else quota.personal_usage(owner)
            )
            counter = StorageUsage.objects.get(
                **({"group": group} if group is not None else {"owner": owner})
            )
            self.assertEqual(
                counter.bytes, expected, f"bucket {owner.username}/{group}"
            )

    def test_create_in_each_bucket(self):
        self._upload("a.txt", b"12345")
        self._upload("b.txt", b"1234567", parent=self.group_root)
        self._upload("c.txt", b"12", owner=self.other)
        self.assertCountersMatch()

    def test_copy_of_a_folder(self):
        folder = tree.create_folder(self.user, "docs")
        self._upload("a.txt", b"12345", parent=folder)
        self._upload("b.txt", b"123", parent=folder)
        tree.copy(folder, self.group_root, self.user)
        tree.copy(folder, None, self.user)
        self.assertCountersMatch()

    def test_content_replacement_grows_and_shrinks(self):
        f = self._upload("a.txt", b"12345")
        update_content(f, ContentFile(b"x" * 50, name="a.txt"), name="a.txt")
        self.assertCountersMatch()
        update_content(f, ContentFile(b"x", name="a.txt"), name="a.txt")
        self.assertCountersMatch()

    def test_content_replacement_from_a_stale_instance(self):
        f = self._upload("a.txt", b"12345")
        stale = File.objects.get(pk=f.pk)
        update_content(f, ContentFile(b"x" * 50, name="a.txt"), name="a.txt")
        update_content(stale, ContentFile(b"x" * 9, name="a.txt"), name="a.txt")
        self.assertCountersMatch()

    def test_content_cleared(self):
        f = self._upload("a.txt", b"12345")
        clear_content(f)
        self.assertCountersMatch()

    def test_move_onto_a_file_of_another_bucket(self):
        target = self._upload("same.txt", b"12", parent=self.group_root)
        source = self._upload("same.txt", b"1234567")
        move_content_from(target, source)
        self.assertCountersMatch()

    def test_soft_delete_and_restore_leave_the_counter_alone(self):
        f = self._upload("a.txt", b"12345")
        trash.soft_delete(f)
        self.assertCountersMatch()
        trash.restore(f)
        self.assertCountersMatch()

    def test_hard_delete_of_a_tree(self):
        folder = tree.create_folder(self.user, "docs")
        self._upload("a.txt", b"12345", parent=folder)
        inner = tree.create_folder(self.user, "inner", parent=folder)
        self._upload("b.txt", b"123", parent=inner)
        trash.soft_delete(folder)
        hard_delete_tree(folder)
        self.assertCountersMatch()

    def test_trash_purge_in_batches(self):
        self._upload("a.txt", b"12345")
        self._upload("b.txt", b"1234567", parent=self.group_root)
        hard_delete_in_batches(File.objects.filter(node_type=File.NodeType.FILE))
        self.assertCountersMatch()

    def test_queryset_delete(self):
        self._upload("a.txt", b"12345")
        File.objects.filter(node_type=File.NodeType.FILE).delete()
        self.assertCountersMatch()

    def test_move_personal_to_group_and_back(self):
        folder = tree.create_folder(self.user, "docs")
        self._upload("a.txt", b"12345", parent=folder)
        trashed = self._upload("gone.txt", b"999", parent=folder)
        trash.soft_delete(trashed)
        tree.move(folder, self.group_root, acting_user=self.user)
        self.assertCountersMatch()
        tree.move(folder, tree.create_folder(self.user, "back"), acting_user=self.user)
        self.assertCountersMatch()

    def test_move_out_of_a_group_hands_the_bytes_to_the_mover(self):
        theirs = self._upload(
            "t.txt", b"1234567", parent=self.group_root, owner=self.other
        )
        mine = tree.create_folder(self.user, "mine")
        tree.move(theirs, mine, acting_user=self.user)
        self.assertCountersMatch()

    def test_move_between_groups(self):
        f = self._upload("a.txt", b"12345", parent=self.group_root)
        tree.move(f, self.ops_root, acting_user=self.user)
        self.assertCountersMatch()

    def test_move_within_a_bucket(self):
        folder = tree.create_folder(self.user, "docs")
        f = self._upload("a.txt", b"12345")
        tree.move(f, folder, acting_user=self.user)
        self.assertCountersMatch()

    def test_disk_sync_registration(self):
        tree.register_disk_file(
            self.user, "seen.txt", None, "files/users/usage/seen.txt", size=4096
        )
        self.assertCountersMatch()

    def test_size_backfill(self):
        from django.core.management import call_command

        f = self._upload("a.txt", b"12345")
        File.objects.filter(pk=f.pk).update(size=None)
        bucket_usage.reconcile()
        call_command("backfill_file_sizes", stdout=io.StringIO())
        self.assertEqual(File.objects.get(pk=f.pk).size, 5)
        self.assertCountersMatch()

    def test_archive_extraction(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("a.txt", b"x" * 300)
            zf.writestr("sub/b.txt", b"y" * 200)
        archive = self._upload("bundle.zip", buf.getvalue())
        extract_zip(archive, self.group_root, acting_user=self.user)
        self.assertCountersMatch()

    def test_group_deletion_hands_the_rows_to_their_owners(self):
        self._upload("a.txt", b"12345", parent=self.group_root)
        self._upload("b.txt", b"1234567", parent=self.group_root, owner=self.other)
        self.second_group.delete()
        self.group.delete()
        self.buckets = [(self.user, None), (self.other, None)]
        self.assertCountersMatch()

    def test_a_rolled_back_write_leaves_the_counter_alone(self):
        from django.db import transaction

        def upload_then_fail():
            with transaction.atomic():
                self._upload("a.txt", b"12345")
                raise RuntimeError

        with self.assertRaises(RuntimeError):
            upload_then_fail()
        self.assertCountersMatch()


class SeedingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="seed", password="pw")

    def test_writes_before_the_first_read_are_counted_once(self):
        tree.create_file(self.user, "a.txt", content=ContentFile(b"12345"))
        StorageUsage.objects.all().delete()
        self.assertEqual(bucket_usage.used_bytes(owner=self.user, group=None), 5)
        tree.create_file(self.user, "b.txt", content=ContentFile(b"123"))
        self.assertEqual(bucket_usage.used_bytes(owner=self.user, group=None), 8)


class ReconcileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="drift", password="pw")
        tree.create_file(self.user, "a.txt", content=ContentFile(b"12345"))
        bucket_usage.used_bytes(owner=self.user, group=None)

    def test_a_drifted_counter_is_reset_and_logged(self):
        StorageUsage.objects.filter(owner=self.user).update(bytes=999)
        with self.assertLogs("workspace.files.services.bucket_usage", "WARNING"):
            self.assertEqual(bucket_usage.reconcile(), 1)
        self.assertEqual(StorageUsage.objects.get(owner=self.user).bytes, 5)

    def test_a_correct_counter_is_left_alone(self):
        self.assertEqual(bucket_usage.reconcile(), 0)
