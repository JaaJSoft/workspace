"""Cleanups that delete in several statements, raced by another connection.

Only PostgreSQL runs these: SQLite takes its single writer lock when a
transaction begins, so nothing can land between two statements of a cleanup.
"""

import threading
from datetime import timedelta
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import Collector
from django.test import TransactionTestCase
from django.utils import timezone

from workspace.files.models import File
from workspace.files.services import trash, tree
from workspace.files.services.extract import _take_back
from workspace.files.services.hard_delete import hard_delete_in_batches

User = get_user_model()

ON_POSTGRES = skipUnless(
    connection.vendor == "postgresql", "row locks and snapshots of PostgreSQL"
)


def in_thread(target):
    def run():
        try:
            target()
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


@ON_POSTGRES
class PurgeRaceTests(TransactionTestCase):
    def test_a_restore_committed_while_the_purge_waits_for_its_lock_is_kept(self):
        user = User.objects.create_user(username="restorer", password="pw")
        node = tree.create_file(user, "kept.txt")
        trash.soft_delete(node, acting_user=user)
        cutoff = timezone.now() - timedelta(days=30)
        File.objects.filter(pk=node.pk).update(deleted_at=cutoff - timedelta(days=15))
        restored = threading.Event()

        # The restore holds the row while the purge reads its keys, then
        # commits once the purge is waiting on that row.
        def restore():
            with transaction.atomic():
                File.objects.select_for_update().get(pk=node.pk)
                File.objects.filter(pk=node.pk).update(deleted_at=None)
                restored.set()
                threading.Event().wait(1)

        thread = in_thread(restore)
        self.assertTrue(restored.wait(5))
        hard_delete_in_batches(File.objects.filter(deleted_at__lte=cutoff))
        thread.join()

        self.assertTrue(File.objects.filter(pk=node.pk).exists())


@ON_POSTGRES
class ExtractionTakeBackRaceTests(TransactionTestCase):
    def test_a_file_added_while_a_created_folder_is_taken_back_is_never_lost(self):
        user = User.objects.create_user(username="extractor", password="pw")
        created = tree.create_folder(user, "new")
        inserted, go, settled = threading.Event(), threading.Event(), threading.Event()
        outcome = {}

        def member_adds_a_file():
            try:
                with transaction.atomic():
                    File.objects.create(
                        owner=user,
                        name="theirs.txt",
                        parent_id=created.pk,
                        node_type=File.NodeType.FILE,
                    )
                    inserted.set()
                    go.wait(5)
                outcome["committed"] = True
            except IntegrityError:
                outcome["refused"] = True
            finally:
                settled.set()

        original = Collector.related_objects
        paused = []

        # The member commits once the folder is chosen for deletion and
        # before the cascade looks for its children.
        def commit_between(self, *args, **kwargs):
            if not paused:
                paused.append(True)
                go.set()
                settled.wait(2)
            return original(self, *args, **kwargs)

        thread = in_thread(member_adds_a_file)
        self.assertTrue(inserted.wait(5))
        with mock.patch.object(Collector, "related_objects", commit_between):
            _take_back([created.pk])
        thread.join()

        theirs = File.objects.filter(name="theirs.txt")
        if outcome.get("committed"):
            self.assertTrue(theirs.exists())
        else:
            self.assertEqual(outcome, {"refused": True})
            self.assertFalse(theirs.exists())
