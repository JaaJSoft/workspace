from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from workspace.imports.models import ImportConnection, ImportJob, ImportJobItem

User = get_user_model()


def _connection(owner, **kwargs):
    defaults = {"provider": "webdav", "label": "My cloud"}
    defaults.update(kwargs)
    return ImportConnection.objects.create(owner=owner, **defaults)


class ImportConnectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")

    def test_secret_round_trips_and_is_not_stored_in_clear(self):
        conn = _connection(self.user)
        conn.set_secret("app-password-123")
        conn.save()
        conn.refresh_from_db()
        self.assertNotIn(b"app-password-123", bytes(conn.secret_encrypted))
        self.assertEqual(conn.get_secret(), "app-password-123")

    def test_secret_defaults_to_empty(self):
        self.assertEqual(_connection(self.user).get_secret(), "")

    def test_oauth2_data_round_trips(self):
        conn = _connection(self.user, provider="google")
        self.assertIsNone(conn.get_oauth2_data())
        conn.set_oauth2_data({"access_token": "tok", "expires_at": 123})
        conn.save()
        conn.refresh_from_db()
        self.assertEqual(
            conn.get_oauth2_data(), {"access_token": "tok", "expires_at": 123}
        )

    def test_deleting_a_connection_removes_its_jobs_and_items(self):
        conn = _connection(self.user)
        job = ImportJob.objects.create(
            owner=self.user, connection=conn, kinds=["files"]
        )
        ImportJobItem.objects.create(
            job=job, kind="files", remote_id="/a.txt", status=ImportJobItem.Status.DONE
        )
        conn.delete()
        self.assertFalse(ImportJob.objects.exists())
        self.assertFalse(ImportJobItem.objects.exists())


class ImportJobTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.conn = _connection(self.user)

    def test_new_job_is_pending_and_not_terminal(self):
        job = ImportJob.objects.create(
            owner=self.user, connection=self.conn, kinds=["files"]
        )
        self.assertEqual(job.status, ImportJob.Status.PENDING)
        self.assertFalse(job.is_terminal)

    def test_terminal_statuses(self):
        job = ImportJob.objects.create(
            owner=self.user, connection=self.conn, kinds=["files"]
        )
        for status in (
            ImportJob.Status.COMPLETED,
            ImportJob.Status.FAILED,
            ImportJob.Status.CANCELLED,
        ):
            job.status = status
            self.assertTrue(job.is_terminal, status)
        job.status = ImportJob.Status.RUNNING
        self.assertFalse(job.is_terminal)

    def test_remote_entry_is_unique_per_job_and_kind(self):
        job = ImportJob.objects.create(
            owner=self.user, connection=self.conn, kinds=["files"]
        )
        ImportJobItem.objects.create(
            job=job, kind="files", remote_id="/a.txt", status=ImportJobItem.Status.DONE
        )
        with self.assertRaises(IntegrityError):
            ImportJobItem.objects.create(
                job=job,
                kind="files",
                remote_id="/a.txt",
                status=ImportJobItem.Status.FAILED,
            )

    def test_same_remote_entry_allowed_across_jobs(self):
        first = ImportJob.objects.create(
            owner=self.user, connection=self.conn, kinds=["files"]
        )
        second = ImportJob.objects.create(
            owner=self.user, connection=self.conn, kinds=["files"]
        )
        for job in (first, second):
            ImportJobItem.objects.create(
                job=job,
                kind="files",
                remote_id="/a.txt",
                status=ImportJobItem.Status.DONE,
            )
        self.assertEqual(ImportJobItem.objects.count(), 2)
