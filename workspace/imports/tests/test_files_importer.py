import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.imports.importers.base import ImportContext, JobFailed, Outcome
from workspace.imports.importers.files import FilesImporter
from workspace.imports.models import ImportConnection, ImportJob, ImportJobItem
from workspace.imports.providers.base import RemoteEntry

from .fakes import fake_provider

User = get_user_model()

MTIME = datetime(2024, 3, 1, 12, 0, tzinfo=UTC)


def _tree():
    return {
        "/": [
            RemoteEntry(id="/Docs", name="Docs", is_dir=True),
            RemoteEntry(
                id="/readme.txt",
                name="readme.txt",
                is_dir=False,
                size=9,
                etag="e1",
                modified_at=MTIME,
                mime_type="text/plain",
            ),
        ],
        "/Docs": [
            RemoteEntry(
                id="/Docs/report.pdf",
                name="report.pdf",
                is_dir=False,
                size=5,
                etag="e2",
            ),
            RemoteEntry(id="/Docs/Archive", name="Archive", is_dir=True),
        ],
        "/Docs/Archive": [
            RemoteEntry(
                id="/Docs/Archive/old.txt",
                name="old.txt",
                is_dir=False,
                size=3,
                etag="e3",
            ),
        ],
    }


class ImporterTestCase(TestCase):
    def setUp(self):
        self._tmpdir = tempfile.mkdtemp()
        self._media = override_settings(MEDIA_ROOT=self._tmpdir)
        self._media.enable()
        self.user = User.objects.create_user(username="alice", password="pw")
        self.provider = fake_provider()
        self.provider.tree = _tree()
        self.conn = ImportConnection.objects.create(
            owner=self.user,
            provider="fake",
            label="Nextcloud",
            base_url="https://x/dav",
            username="a",
        )
        self.importer = FilesImporter()

    def tearDown(self):
        self._media.disable()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        cache.clear()

    def _job(self, **options):
        return ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            options={"files": options},
            status=ImportJob.Status.RUNNING,
        )

    def _run(self, job, deadline=None):
        ctx = ImportContext(job, self.provider, "files", deadline=deadline)
        return self.importer.run(ctx)

    def _files(self):
        return {
            f.name: f
            for f in File.objects.filter(owner=self.user, deleted_at__isnull=True)
        }

    def _path(self, file_obj):
        parts = []
        while file_obj is not None:
            parts.append(file_obj.name)
            file_obj = file_obj.parent
        return "/".join(reversed(parts))


class FilesImporterTests(ImporterTestCase):
    def test_imports_the_tree_under_a_root_folder(self):
        job = self._job()
        self.assertIs(self._run(job), Outcome.DONE)

        files = self._files()
        self.assertEqual(
            {self._path(f) for f in files.values()},
            {
                "Nextcloud import",
                "Nextcloud import/readme.txt",
                "Nextcloud import/Docs",
                "Nextcloud import/Docs/report.pdf",
                "Nextcloud import/Docs/Archive",
                "Nextcloud import/Docs/Archive/old.txt",
            },
        )
        with files["readme.txt"].content.open("rb") as fh:
            self.assertEqual(fh.read(), b"content of readme.txt")
        self.assertEqual(files["readme.txt"].size, len(b"content of readme.txt"))
        self.assertEqual(files["readme.txt"].updated_at, MTIME)

        stats = job.stats["files"]
        self.assertEqual(stats["phase"], "done")
        self.assertEqual(stats["total_files"], 3)
        self.assertEqual(stats["total_bytes"], 17)
        self.assertEqual(stats["files"], 3)
        self.assertEqual(stats["folders"], 3)  # root + Docs + Archive
        self.assertTrue(stats["planned"])
        self.assertEqual(stats["root_folder"], str(files["Nextcloud import"].uuid))

        items = {i.remote_id: i for i in ImportJobItem.objects.filter(job=job)}
        self.assertEqual(
            set(items), {"/readme.txt", "/Docs/report.pdf", "/Docs/Archive/old.txt"}
        )
        self.assertEqual(items["/readme.txt"].status, ImportJobItem.Status.DONE)
        self.assertEqual(items["/readme.txt"].target_uuid, files["readme.txt"].uuid)
        self.assertEqual(items["/readme.txt"].remote_etag, "e1")
        self.assertTrue(self.provider.last_source.closed)

    def test_source_path_and_destination_without_root_folder(self):
        dest = FileService.create_folder(self.user, "Inbox")
        job = self._job(
            source_path="/Docs", destination=str(dest.uuid), create_root_folder=False
        )
        self._run(job)
        self.assertEqual(
            {self._path(f) for f in self._files().values()},
            {"Inbox", "Inbox/report.pdf", "Inbox/Archive", "Inbox/Archive/old.txt"},
        )

    def test_same_name_folders_are_reused(self):
        root = FileService.create_folder(self.user, "Nextcloud import")
        FileService.create_folder(self.user, "docs", root)  # case-insensitive match
        self._run(self._job())
        self.assertEqual(
            File.objects.filter(
                owner=self.user, node_type=File.NodeType.FOLDER
            ).count(),
            3,
        )

    def test_conflict_rename_is_the_default(self):
        root = FileService.create_folder(self.user, "Nextcloud import")
        FileService.create_file(
            self.user, "readme.txt", root, content=ContentFile(b"mine")
        )
        job = self._job()
        self._run(job)
        names = sorted(
            f.name
            for f in File.objects.filter(parent=root, node_type=File.NodeType.FILE)
        )
        self.assertEqual(names, ["readme (Copy).txt", "readme.txt"])
        self.assertEqual(job.stats["files"]["files"], 3)

    def test_conflict_skip(self):
        root = FileService.create_folder(self.user, "Nextcloud import")
        mine = FileService.create_file(
            self.user, "readme.txt", root, content=ContentFile(b"mine")
        )
        job = self._job(on_conflict="skip")
        self._run(job)
        mine.refresh_from_db()
        with mine.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"mine")
        self.assertEqual(job.stats["files"]["skipped"], 1)
        self.assertEqual(job.stats["files"]["files"], 2)
        item = ImportJobItem.objects.get(job=job, remote_id="/readme.txt")
        self.assertEqual(item.status, ImportJobItem.Status.SKIPPED)
        self.assertEqual(item.target_uuid, mine.uuid)

    def test_conflict_replace(self):
        root = FileService.create_folder(self.user, "Nextcloud import")
        mine = FileService.create_file(
            self.user, "readme.txt", root, content=ContentFile(b"mine")
        )
        self._run(self._job(on_conflict="replace"))
        mine.refresh_from_db()
        with mine.content.open("rb") as fh:
            self.assertEqual(fh.read(), b"content of readme.txt")
        self.assertEqual(
            File.objects.filter(parent=root, node_type=File.NodeType.FILE).count(), 1
        )

    def test_second_run_only_imports_what_changed(self):
        self._run(self._job())
        self.provider.tree["/"].append(
            RemoteEntry(id="/new.txt", name="new.txt", is_dir=False, etag="n1")
        )
        self.provider.tree["/"][1] = RemoteEntry(
            id="/readme.txt", name="readme.txt", is_dir=False, etag="e1-changed"
        )
        job = self._job(on_conflict="replace")
        self._run(job)
        self.assertEqual(job.stats["files"]["files"], 2)
        self.assertEqual(
            sorted(self.provider.last_source.opened), ["/new.txt", "/readme.txt"]
        )

    def test_quota_is_checked_before_any_write(self):
        with override_settings(STORAGE_QUOTA_BYTES=10):
            with self.assertRaisesRegex(JobFailed, "Not enough space"):
                self._run(self._job())
        self.assertFalse(File.objects.filter(owner=self.user).exists())

    def test_listing_failure_during_planning_fails_the_job(self):
        self.provider.fail_list.add("/Docs")
        with self.assertRaisesRegex(JobFailed, "Could not list '/Docs'"):
            self._run(self._job())

    def test_a_vanished_file_is_recorded_and_the_import_goes_on(self):
        self.provider.fail_open.add("/Docs/report.pdf")
        job = self._job()
        self.assertIs(self._run(job), Outcome.DONE)
        item = ImportJobItem.objects.get(job=job, remote_id="/Docs/report.pdf")
        self.assertEqual(item.status, ImportJobItem.Status.FAILED)
        self.assertIn("vanished", item.error)
        self.assertEqual(job.stats["files"]["failed"], 1)
        self.assertEqual(job.stats["files"]["files"], 2)

    @override_settings(IMPORTS_MAX_CONSECUTIVE_ERRORS=2)
    def test_too_many_consecutive_errors_fail_the_job(self):
        self.provider.fail_open.update(
            {"/readme.txt", "/Docs/report.pdf", "/Docs/Archive/old.txt"}
        )
        with self.assertRaisesRegex(JobFailed, "consecutive errors"):
            self._run(self._job())

    def test_cancellation_stops_between_files_and_keeps_what_was_done(self):
        job = self._job()
        original = ImportContext.cancelled

        def cancel_after_first_file(ctx):
            return bool(ctx.stats.get("files"))

        with patch.object(ImportContext, "cancelled", cancel_after_first_file):
            self.assertIs(self._run(job), Outcome.CANCELLED)
        self.assertEqual(job.stats["files"]["files"], 1)
        self.assertEqual(ImportJobItem.objects.filter(job=job).count(), 1)
        self.assertIs(original, ImportContext.cancelled)

    def test_deadline_pauses_and_a_later_slice_resumes_without_duplicates(self):
        job = self._job()
        self.assertIs(
            self._run(job, deadline=timezone.now() - timedelta(seconds=1)),
            Outcome.PAUSED,
        )
        self.assertEqual(job.stats["files"]["phase"], "listing")

        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(job.stats["files"]["files"], 3)
        self.assertEqual(File.objects.filter(owner=self.user).count(), 6)
        # and running it once more is a no-op
        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(File.objects.filter(owner=self.user).count(), 6)

    def test_missing_destination_fails_the_job(self):
        job = self._job(
            destination="00000000-0000-0000-0000-000000000000", create_root_folder=False
        )
        with self.assertRaisesRegex(JobFailed, "destination folder no longer exists"):
            self._run(job)


class CopyPhaseListingFailureTests(ImporterTestCase):
    def test_unlistable_folder_is_recorded_and_the_rest_continues(self):
        job = self._job()
        job.stats = {"files": {"planned": True}}  # skip the listing phase
        self.provider.fail_list.add("/Docs/Archive")
        self.assertIs(self._run(job), Outcome.DONE)
        item = ImportJobItem.objects.get(job=job, remote_id="/Docs/Archive")
        self.assertEqual(item.status, ImportJobItem.Status.FAILED)
        self.assertIn("cannot list", item.error)
        self.assertEqual(job.stats["files"]["files"], 2)
        self.assertEqual(job.stats["files"]["failed"], 1)
