"""Cleanups that delete in several statements, raced by another connection.

The racing transaction commits once the cleanup is seen waiting on its lock.
PostgreSQL only: in production SQLite serializes writers with BEGIN IMMEDIATE
and busy_timeout, but the test database is a shared-cache in-memory one, whose
table locks refuse a second connection instead of making it wait.
"""

import threading
import time
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


def another_connection_waits_on_a_lock(timeout=10):
    """Whether another backend is blocked on a lock, polled from this one."""
    deadline = time.monotonic() + timeout
    with connection.cursor() as cursor:
        while True:
            # Statistics views are frozen for the transaction unless cleared.
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                " AND wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            )
            if cursor.fetchone()[0]:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)


@ON_POSTGRES
class PurgeRaceTests(TransactionTestCase):
    def test_a_restore_committed_while_the_purge_waits_for_its_lock_is_kept(self):
        user = User.objects.create_user(username="restorer", password="pw")
        node = tree.create_file(user, "kept.txt")
        trash.soft_delete(node, acting_user=user)
        cutoff = timezone.now() - timedelta(days=30)
        File.objects.filter(pk=node.pk).update(deleted_at=cutoff - timedelta(days=15))
        restored, purging = threading.Event(), threading.Event()
        outcome = {}

        # The restore is written but not committed while the purge reads its
        # keys, and commits once the purge waits on what the restore holds.
        def restore():
            with transaction.atomic():
                trash.restore(File.objects.get(pk=node.pk), acting_user=user)
                restored.set()
                purging.wait(5)
                outcome["purge_waited"] = another_connection_waits_on_a_lock()

        thread = in_thread(restore)
        self.assertTrue(restored.wait(5))
        purging.set()
        hard_delete_in_batches(File.objects.filter(deleted_at__lte=cutoff))
        thread.join()

        self.assertEqual(outcome, {"purge_waited": True})
        node = File.objects.get(pk=node.pk)
        self.assertIsNone(node.deleted_at)


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
        # before the cascade looks for its children - or waits on its lock.
        def commit_between(self, *args, **kwargs):
            if not paused:
                paused.append(True)
                go.set()
                deadline = time.monotonic() + 10
                while not settled.wait(0.02) and time.monotonic() < deadline:
                    if another_connection_waits_on_a_lock(timeout=0):
                        outcome["member_waited"] = True
                        break
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
            self.assertEqual(outcome, {"member_waited": True, "refused": True})
            self.assertFalse(theirs.exists())
