"""Tests for workspace.files.tasks Celery entry points.

sync_user_files and sync_folder delegate the actual disk work to
FileSyncService, which we mock. The tests focus on real orchestration
logic: active-user filtering, fan-out and its lock guard, access control
via FileService.user_files_qs, and argument passing.

purge_trash is not mocked at all — it runs the real ORM filter against
File rows created by the test.

generate_thumbnails is deliberately NOT covered here: its body is a
pure pass-through to generate_missing_thumbnails, so testing it through
the task wrapper would only assert ``mock.assert_called_once()``. The
underlying helper should be tested directly instead.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.files import tasks as files_tasks
from workspace.files.models import File
from workspace.files.sync import SyncResult

User = get_user_model()


class SyncAllUsersDispatchTests(TestCase):
    """The beat entry point only fans out - one task per active user."""

    @classmethod
    def setUpTestData(cls):
        # Data migrations can seed extra active users (the AI assistant bot
        # from ai.0002_create_default_bot when AI_API_KEY is configured).
        # sync_all_users dispatches for every active user, so deactivate any
        # pre-existing ones to keep the task's input deterministic.
        User.objects.update(is_active=False)
        cls.alice = User.objects.create_user(username="alice", password="pass")
        cls.bob = User.objects.create_user(username="bob", password="pass")
        cls.inactive = User.objects.create_user(
            username="ghost",
            password="pass",
            is_active=False,
        )

    def test_dispatches_one_task_per_active_user(self):
        with mock.patch.object(files_tasks.sync_user_files, "delay") as delay:
            result = files_tasks.sync_all_users.run()

        self.assertEqual(result["users_dispatched"], 2)
        self.assertEqual(result["enqueue_failures"], 0)
        dispatched = {call.args[0] for call in delay.call_args_list}
        self.assertEqual(dispatched, {self.alice.pk, self.bob.pk})

    def test_does_not_dispatch_for_inactive_users(self):
        with mock.patch.object(files_tasks.sync_user_files, "delay") as delay:
            files_tasks.sync_all_users.run()

        dispatched = {call.args[0] for call in delay.call_args_list}
        self.assertNotIn(self.inactive.pk, dispatched)

    def test_broker_failure_for_one_user_does_not_abort_the_fan_out(self):
        # A refusal on the first enqueue must not cost every later user their
        # sync - the dispatcher is the single point of failure for all of them.
        def _flaky(user_id):
            if user_id == self.alice.pk:
                raise RuntimeError("broker down")

        with mock.patch.object(
            files_tasks.sync_user_files, "delay", side_effect=_flaky
        ) as delay:
            result = files_tasks.sync_all_users.run()

        self.assertEqual(delay.call_count, 2)
        self.assertEqual(result["users_dispatched"], 1)
        self.assertEqual(result["enqueue_failures"], 1)


class SyncUserFilesTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="walker", password="pass")

    def tearDown(self):
        cache.clear()

    def _fake_service(self, result=None):
        fake = mock.Mock()
        fake.sync_user_recursive.return_value = result or SyncResult(
            files_created=2,
            folders_created=1,
            files_soft_deleted=3,
            folders_soft_deleted=4,
            errors=["boom"],
        )
        return fake

    def test_returns_the_walk_result(self):
        fake = self._fake_service()
        with mock.patch("workspace.files.sync.FileSyncService", return_value=fake):
            result = files_tasks.sync_user_files.run(self.user.pk)

        fake.sync_user_recursive.assert_called_once()
        self.assertEqual(fake.sync_user_recursive.call_args.args[0].pk, self.user.pk)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["files_created"], 2)
        self.assertEqual(result["folders_created"], 1)
        self.assertEqual(result["files_soft_deleted"], 3)
        self.assertEqual(result["folders_soft_deleted"], 4)
        self.assertEqual(result["errors"], ["boom"])

    def test_skips_when_a_sync_is_already_running_for_that_user(self):
        # Overlap guard: beat fires on a fixed period, so a walk that outlives
        # the period would otherwise have a second copy stacked on top of it.
        cache.add(
            f"files:sync:user:{self.user.pk}",
            "locked",
            files_tasks.SYNC_USER_LOCK_TTL,
        )

        fake = self._fake_service()
        with mock.patch("workspace.files.sync.FileSyncService", return_value=fake):
            result = files_tasks.sync_user_files.run(self.user.pk)

        fake.sync_user_recursive.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "already_running")

    def test_releases_the_lock_after_a_successful_run(self):
        fake = self._fake_service()
        with mock.patch("workspace.files.sync.FileSyncService", return_value=fake):
            files_tasks.sync_user_files.run(self.user.pk)
            files_tasks.sync_user_files.run(self.user.pk)

        # A lock leaked by the first run would make the second a no-op and
        # stall the user's sync until the TTL expired.
        self.assertEqual(fake.sync_user_recursive.call_count, 2)

    def test_releases_the_lock_when_the_walk_raises(self):
        fake = mock.Mock()
        fake.sync_user_recursive.side_effect = OSError("mount vanished")

        with mock.patch("workspace.files.sync.FileSyncService", return_value=fake):
            with self.assertRaises(OSError):
                files_tasks.sync_user_files.run(self.user.pk)

        self.assertIsNone(cache.get(f"files:sync:user:{self.user.pk}"))

    def test_inactive_user_is_not_synced(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        fake = self._fake_service()
        with mock.patch("workspace.files.sync.FileSyncService", return_value=fake):
            result = files_tasks.sync_user_files.run(self.user.pk)

        fake.sync_user_recursive.assert_not_called()
        self.assertEqual(result["status"], "not_found")

    def test_missing_user_is_reported_not_raised(self):
        # The dispatcher enqueues by pk; a user deleted in between must not
        # turn into a retrying/erroring task.
        fake = self._fake_service()
        with mock.patch("workspace.files.sync.FileSyncService", return_value=fake):
            result = files_tasks.sync_user_files.run(999_999)

        self.assertEqual(result["status"], "not_found")

    def test_malicious_username_cannot_forge_log_lines(self):
        # A username carrying CR/LF must not be able to inject a second
        # (forged) log line - it has to be flattened before logging
        # (CWE-117 log injection).
        evil = User.objects.create_user(
            username="evil\r\nINFO:root:forged admin login",
            password="pass",
        )

        fake_service = mock.Mock()
        fake_service.sync_user_recursive.return_value = SyncResult()

        with mock.patch(
            "workspace.files.sync.FileSyncService",
            return_value=fake_service,
        ):
            with self.assertLogs("workspace.files.tasks", level="INFO") as cm:
                files_tasks.sync_user_files.run(evil.pk)

        per_user_lines = [m for m in cm.output if "Syncing files for user" in m]
        self.assertEqual(len(per_user_lines), 1)
        line = per_user_lines[0]
        self.assertNotIn("\r", line)
        self.assertNotIn("\n", line)
        # The username content is preserved, just flattened onto one line.
        self.assertIn("forged admin login", line)

    def test_skip_log_line_also_scrubs_the_username(self):
        evil = User.objects.create_user(
            username="skip\r\nINFO:root:forged",
            password="pass",
        )
        cache.add(f"files:sync:user:{evil.pk}", "locked", 60)

        with self.assertLogs("workspace.files.tasks", level="INFO") as cm:
            files_tasks.sync_user_files.run(evil.pk)

        skip_lines = [m for m in cm.output if "already running" in m]
        self.assertEqual(len(skip_lines), 1)
        self.assertNotIn("\r", skip_lines[0])
        self.assertNotIn("\n", skip_lines[0])


class PurgeTrashTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="owner", password="pass")

    def _make_file(self, name, node_type, deleted_days_ago=None):
        f = File.objects.create(
            owner=self.user,
            name=name,
            node_type=node_type,
        )
        if deleted_days_ago is not None:
            File.objects.filter(pk=f.pk).update(
                deleted_at=timezone.now() - timedelta(days=deleted_days_ago),
            )
        return f

    @override_settings(TRASH_RETENTION_DAYS=30)
    def test_deletes_old_trashed_entries(self):
        old_file = self._make_file("old.txt", File.NodeType.FILE, deleted_days_ago=45)
        old_folder = self._make_file(
            "old-dir", File.NodeType.FOLDER, deleted_days_ago=60
        )
        recent_file = self._make_file(
            "recent.txt", File.NodeType.FILE, deleted_days_ago=5
        )
        live_file = self._make_file("live.txt", File.NodeType.FILE)

        result = files_tasks.purge_trash.run()

        self.assertEqual(result["files_deleted"], 1)
        self.assertEqual(result["folders_deleted"], 1)
        self.assertEqual(result["retention_days"], 30)

        self.assertFalse(File.objects.filter(pk=old_file.pk).exists())
        self.assertFalse(File.objects.filter(pk=old_folder.pk).exists())
        self.assertTrue(File.objects.filter(pk=recent_file.pk).exists())
        self.assertTrue(File.objects.filter(pk=live_file.pk).exists())

    @override_settings(TRASH_RETENTION_DAYS=30)
    def test_files_and_folders_counted_in_a_single_pass(self):
        """The files/folders breakdown comes from one aggregate query, not a
        separate count() per node type. The cascade delete that follows is
        intrinsic to File.delete() and left unmeasured here - only the
        counting pass is pinned."""
        self._make_file("old.txt", File.NodeType.FILE, deleted_days_ago=45)
        self._make_file("old-dir", File.NodeType.FOLDER, deleted_days_ago=60)

        with CaptureQueriesContext(connection) as ctx:
            result = files_tasks.purge_trash.run()

        self.assertEqual(result["files_deleted"], 1)
        self.assertEqual(result["folders_deleted"], 1)
        count_queries = [q for q in ctx.captured_queries if "COUNT" in q["sql"].upper()]
        self.assertEqual(len(count_queries), 1)

    @override_settings(TRASH_RETENTION_DAYS=30)
    def test_noop_when_trash_empty(self):
        self._make_file("live.txt", File.NodeType.FILE)
        result = files_tasks.purge_trash.run()

        self.assertEqual(
            result,
            {"files_deleted": 0, "folders_deleted": 0, "retention_days": 30},
        )

    def test_retention_days_defaults_to_30_when_unset(self):
        from django.conf import settings as dj_settings

        # settings.py always defines TRASH_RETENTION_DAYS, so delete it
        # inside an empty override block (the UserSettingsHolder restores
        # it on exit) to actually exercise the getattr fallback in
        # purge_trash. Overriding with None would not do it: the attribute
        # would still exist and getattr would return None, not 30.
        with self.settings():
            del dj_settings.TRASH_RETENTION_DAYS
            self.assertFalse(hasattr(dj_settings, "TRASH_RETENTION_DAYS"))
            result = files_tasks.purge_trash.run()

        self.assertEqual(result["retention_days"], 30)


class SyncFolderTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="dave", password="pass")
        cls.folder = File.objects.create(
            owner=cls.user,
            name="Work",
            node_type=File.NodeType.FOLDER,
        )

    def _fake_service(self, result=None):
        fake = mock.Mock()
        fake.sync_folder_shallow.return_value = result or SyncResult(
            files_created=4,
            folders_created=1,
            files_soft_deleted=0,
            folders_soft_deleted=0,
            errors=[],
        )
        return fake

    def test_root_sync_when_no_folder_uuid(self):
        fake = self._fake_service()
        with mock.patch(
            "workspace.files.sync.FileSyncService",
            return_value=fake,
        ):
            result = files_tasks.sync_folder.run(self.user.pk)

        fake.sync_folder_shallow.assert_called_once_with(self.user, None)
        self.assertEqual(result["files_created"], 4)
        self.assertEqual(result["folders_created"], 1)
        self.assertEqual(result["errors"], [])

    def test_sync_specific_folder(self):
        fake = self._fake_service()
        with mock.patch(
            "workspace.files.sync.FileSyncService",
            return_value=fake,
        ):
            files_tasks.sync_folder.run(self.user.pk, folder_uuid=str(self.folder.uuid))

        call = fake.sync_folder_shallow.call_args
        self.assertEqual(call.args[0], self.user)
        passed_folder = call.args[1]
        self.assertEqual(passed_folder.pk, self.folder.pk)

    def test_missing_user_raises(self):
        with self.assertRaises(User.DoesNotExist):
            files_tasks.sync_folder.run(user_id=999_999)
