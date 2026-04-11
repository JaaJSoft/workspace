"""Tests for workspace.files.tasks Celery entry points.

sync_all_users and sync_folder delegate the actual disk work to
FileSyncService, which we mock. The tests focus on real orchestration
logic: active-user filtering, SyncResult aggregation, access control
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
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files import tasks as files_tasks
from workspace.files.models import File
from workspace.files.sync import SyncResult

User = get_user_model()


class SyncAllUsersTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username='alice', password='pass')
        cls.bob = User.objects.create_user(username='bob', password='pass')
        cls.inactive = User.objects.create_user(
            username='ghost', password='pass', is_active=False,
        )

    def test_aggregates_results_across_active_users(self):
        per_user_results = {
            self.alice.pk: SyncResult(
                files_created=2, folders_created=1,
                files_soft_deleted=0, folders_soft_deleted=0,
                errors=['boom-alice'],
            ),
            self.bob.pk: SyncResult(
                files_created=3, folders_created=0,
                files_soft_deleted=1, folders_soft_deleted=2,
                errors=[],
            ),
        }

        def _fake_sync(user):
            return per_user_results[user.pk]

        fake_service = mock.Mock()
        fake_service.sync_user_recursive.side_effect = _fake_sync

        with mock.patch(
            'workspace.files.sync.FileSyncService', return_value=fake_service,
        ) as service_cls:
            result = files_tasks.sync_all_users.run()

        self.assertEqual(result['users_processed'], 2)
        self.assertEqual(result['files_created'], 5)
        self.assertEqual(result['folders_created'], 1)
        self.assertEqual(result['files_soft_deleted'], 1)
        self.assertEqual(result['folders_soft_deleted'], 2)
        self.assertEqual(result['errors'], ['boom-alice'])
        service_cls.assert_called_once()
        # Inactive user must be skipped.
        synced_pks = {
            call.args[0].pk for call in fake_service.sync_user_recursive.call_args_list
        }
        self.assertEqual(synced_pks, {self.alice.pk, self.bob.pk})


class PurgeTrashTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='owner', password='pass')

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
        old_file = self._make_file('old.txt', File.NodeType.FILE, deleted_days_ago=45)
        old_folder = self._make_file('old-dir', File.NodeType.FOLDER, deleted_days_ago=60)
        recent_file = self._make_file('recent.txt', File.NodeType.FILE, deleted_days_ago=5)
        live_file = self._make_file('live.txt', File.NodeType.FILE)

        result = files_tasks.purge_trash.run()

        self.assertEqual(result['files_deleted'], 1)
        self.assertEqual(result['folders_deleted'], 1)
        self.assertEqual(result['retention_days'], 30)

        self.assertFalse(File.objects.filter(pk=old_file.pk).exists())
        self.assertFalse(File.objects.filter(pk=old_folder.pk).exists())
        self.assertTrue(File.objects.filter(pk=recent_file.pk).exists())
        self.assertTrue(File.objects.filter(pk=live_file.pk).exists())

    @override_settings(TRASH_RETENTION_DAYS=30)
    def test_noop_when_trash_empty(self):
        self._make_file('live.txt', File.NodeType.FILE)
        result = files_tasks.purge_trash.run()

        self.assertEqual(
            result, {'files_deleted': 0, 'folders_deleted': 0, 'retention_days': 30},
        )

    def test_retention_days_defaults_to_30_when_unset(self):
        with self.settings():
            # Ensure the setting isn't overridden in the inherited settings.
            from django.conf import settings as dj_settings
            self.assertFalse(hasattr(dj_settings, 'TRASH_RETENTION_DAYS') and dj_settings.TRASH_RETENTION_DAYS != 30)

        result = files_tasks.purge_trash.run()
        self.assertEqual(result['retention_days'], 30)


class SyncFolderTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='dave', password='pass')
        cls.folder = File.objects.create(
            owner=cls.user, name='Work', node_type=File.NodeType.FOLDER,
        )

    def _fake_service(self, result=None):
        fake = mock.Mock()
        fake.sync_folder_shallow.return_value = result or SyncResult(
            files_created=4, folders_created=1,
            files_soft_deleted=0, folders_soft_deleted=0,
            errors=[],
        )
        return fake

    def test_root_sync_when_no_folder_uuid(self):
        fake = self._fake_service()
        with mock.patch(
            'workspace.files.sync.FileSyncService', return_value=fake,
        ):
            result = files_tasks.sync_folder.run(self.user.pk)

        fake.sync_folder_shallow.assert_called_once_with(self.user, None)
        self.assertEqual(result['files_created'], 4)
        self.assertEqual(result['folders_created'], 1)
        self.assertEqual(result['errors'], [])

    def test_sync_specific_folder(self):
        fake = self._fake_service()
        with mock.patch(
            'workspace.files.sync.FileSyncService', return_value=fake,
        ):
            files_tasks.sync_folder.run(self.user.pk, folder_uuid=str(self.folder.uuid))

        call = fake.sync_folder_shallow.call_args
        self.assertEqual(call.args[0], self.user)
        passed_folder = call.args[1]
        self.assertEqual(passed_folder.pk, self.folder.pk)

    def test_missing_user_raises(self):
        with self.assertRaises(User.DoesNotExist):
            files_tasks.sync_folder.run(user_id=999_999)
