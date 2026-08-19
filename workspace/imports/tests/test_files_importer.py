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
        # One live job per connection: the previous one is over by now.
        ImportJob.objects.filter(connection=self.conn).update(
            status=ImportJob.Status.COMPLETED
        )
        return ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            options={"files": options},
            status=ImportJob.Status.RUNNING,
        )

    def _run(self, job, deadline=None):
        ctx = ImportContext(job, self.provider, self.importer, deadline=deadline)
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
        self.assertNotIn("plan_stack", stats)
        self.assertNotIn("copy_stack", stats)
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
        FileService.create_folder(self.user, "Docs", root)
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
        self._run(self._job(on_conflict="replace"))
        self.provider.tree["/"].append(
            RemoteEntry(id="/new.txt", name="new.txt", is_dir=False, etag="n1")
        )
        self.provider.tree["/"][1] = RemoteEntry(
            id="/readme.txt", name="readme.txt", is_dir=False, etag="e1-changed"
        )
        job = self._job(on_conflict="replace")
        self._run(job)
        self.assertEqual(job.stats["files"]["files"], 2)
        self.assertEqual(job.stats["files"]["unchanged"], 2)
        self.assertEqual(job.stats["files"]["total_files"], 4)
        self.assertEqual(
            sorted(self.provider.last_source.opened), ["/new.txt", "/readme.txt"]
        )

    def test_a_job_with_other_options_does_not_inherit_the_done_list(self):
        self._run(self._job())
        other = FileService.create_folder(self.user, "Elsewhere")
        job = self._job(destination=str(other.uuid), create_root_folder=False)
        self._run(job)
        self.assertEqual(job.stats["files"]["files"], 3)
        self.assertEqual(
            File.objects.filter(parent=other, node_type=File.NodeType.FILE).count(), 1
        )

    def test_a_locally_deleted_file_is_imported_again(self):
        self._run(self._job())
        gone = File.objects.get(owner=self.user, name="report.pdf")
        FileService.soft_delete(gone)
        job = self._job()
        self._run(job)
        self.assertEqual(job.stats["files"]["files"], 1)
        self.assertEqual(self.provider.last_source.opened, ["/Docs/report.pdf"])

    def test_entries_without_any_version_marker_are_always_fetched(self):
        self.provider.tree["/"] = [
            RemoteEntry(id="/blank.txt", name="blank.txt", is_dir=False)
        ]
        self._run(self._job(on_conflict="replace"))
        job = self._job(on_conflict="replace")
        self._run(job)
        self.assertEqual(job.stats["files"]["files"], 1)

    def test_size_and_mtime_stand_in_for_a_missing_etag(self):
        entry = RemoteEntry(id="/x", name="x", is_dir=False, size=3, modified_at=MTIME)
        self.assertEqual(entry.fingerprint, f"3:{MTIME.timestamp():.0f}")
        self.assertEqual(
            RemoteEntry(id="/x", name="x", is_dir=False, etag="e").fingerprint, "e"
        )
        self.assertEqual(RemoteEntry(id="/x", name="x", is_dir=False).fingerprint, "")

    def test_quota_is_checked_before_any_write(self):
        with override_settings(STORAGE_QUOTA_BYTES=10):
            with self.assertRaisesRegex(JobFailed, "Not enough space"):
                self._run(self._job())
        self.assertFalse(File.objects.filter(owner=self.user).exists())

    def test_quota_check_ignores_what_is_already_imported(self):
        self._run(self._job())
        self.provider.tree["/"].append(
            RemoteEntry(id="/tiny.txt", name="tiny.txt", is_dir=False, size=1, etag="t")
        )
        used = FileService.storage_used(self.user)
        with override_settings(STORAGE_QUOTA_BYTES=used + 100):
            job = self._job()
            self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(job.stats["files"]["files"], 1)

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

    def test_names_the_files_module_refuses_are_recorded_per_entry(self):
        self.provider.tree["/"] = [
            RemoteEntry(id="/a%2Fb", name="a/b.txt", is_dir=False, etag="1"),
            RemoteEntry(id="/long", name="x" * 300 + ".txt", is_dir=False, etag="2"),
            RemoteEntry(
                id="/mime",
                name="weird.bin",
                is_dir=False,
                etag="3",
                mime_type="application/" + "x" * 200,
            ),
        ]
        job = self._job()
        self.assertIs(self._run(job), Outcome.DONE)
        names = sorted(
            f.name
            for f in File.objects.filter(owner=self.user, node_type=File.NodeType.FILE)
        )
        self.assertEqual(names, ["a-b.txt", "weird.bin", "x" * 255])
        self.assertEqual(job.stats["files"]["files"], 3)
        self.assertLessEqual(len(File.objects.get(name="weird.bin").mime_type), 100)

    def test_storage_layer_errors_are_recorded_per_entry_and_the_job_goes_on(self):
        job = self._job()
        with patch(
            "workspace.imports.importers.files.FileService.create_file",
            side_effect=ValueError("bad name"),
        ):
            self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(job.stats["files"]["failed"], 3)
        self.assertEqual(
            ImportJobItem.objects.filter(
                job=job, status=ImportJobItem.Status.FAILED
            ).count(),
            3,
        )
        self.assertIn(
            "not accepted", ImportJobItem.objects.filter(job=job).first().error
        )

    def test_root_folder_name_is_sanitised(self):
        self.conn.label = "Nextcloud/Home " + "x" * 300
        self.conn.save()
        job = self._job()
        self.assertIs(self._run(job), Outcome.DONE)
        root = File.objects.get(uuid=job.stats["files"]["root_folder"])
        self.assertNotIn("/", root.name)
        self.assertLessEqual(len(root.name), 255)
        self.assertTrue(root.name.startswith("Nextcloud-Home"))

    def test_replaced_file_keeps_a_fresh_modification_stamp(self):
        root = FileService.create_folder(self.user, "Nextcloud import")
        mine = FileService.create_file(
            self.user, "readme.txt", root, content=ContentFile(b"mine")
        )
        before = mine.updated_at
        self._run(self._job(on_conflict="replace"))
        mine.refresh_from_db()
        self.assertGreater(mine.updated_at, before)
        self.assertNotEqual(mine.updated_at, MTIME)

    def test_folders_are_matched_case_sensitively(self):
        self.provider.tree["/"] = [
            RemoteEntry(id="/Docs", name="Docs", is_dir=True),
            RemoteEntry(id="/docs", name="docs", is_dir=True),
        ]
        self.provider.tree["/docs"] = [
            RemoteEntry(id="/docs/x.txt", name="x.txt", is_dir=False, etag="x")
        ]
        self._run(self._job())
        root = File.objects.get(owner=self.user, name="Nextcloud import")
        names = sorted(
            File.objects.filter(
                parent=root, node_type=File.NodeType.FOLDER
            ).values_list("name", flat=True)
        )
        self.assertEqual(names, ["Docs", "docs"])

    def test_listing_phase_resumes_from_its_persisted_stack(self):
        job = self._job()
        calls = {"n": 0}
        original = ImportContext.out_of_time

        def out_of_time_after_two_listings(ctx):
            return calls["n"] >= 2

        def counting_list(source_self, entry_id):
            calls["n"] += 1
            yield from self.provider.tree.get(entry_id, [])

        from workspace.imports.tests.fakes import FakeFileSource

        with (
            patch.object(FakeFileSource, "list_dir", counting_list),
            patch.object(ImportContext, "out_of_time", out_of_time_after_two_listings),
        ):
            self.assertIs(self._run(job), Outcome.PAUSED)
        self.assertIn("plan_stack", job.stats["files"])
        self.assertEqual(
            job.stats["files"]["total_files"], 2
        )  # / and /Docs listed, /Docs/Archive pending
        self.assertIs(original, ImportContext.out_of_time)

        self.assertIs(self._run(job), Outcome.DONE)
        self.assertEqual(job.stats["files"]["total_files"], 3)
        self.assertEqual(job.stats["files"]["files"], 3)

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


class NoMarkerResumeTests(ImporterTestCase):
    def test_entries_without_marker_are_not_copied_twice_after_a_pause(self):
        self.provider.tree["/"] = [
            RemoteEntry(id="/a.txt", name="a.txt", is_dir=False),
            RemoteEntry(id="/b.txt", name="b.txt", is_dir=False),
        ]
        job = self._job()
        job.stats = {"files": {"planned": True}}

        def out_of_time_after_first_file(ctx):
            return bool(ctx.stats.get("files"))

        with patch.object(ImportContext, "out_of_time", out_of_time_after_first_file):
            self.assertIs(self._run(job), Outcome.PAUSED)
        self.assertIs(self._run(job), Outcome.DONE)
        names = sorted(
            File.objects.filter(
                owner=self.user, node_type=File.NodeType.FILE
            ).values_list("name", flat=True)
        )
        self.assertEqual(names, ["a.txt", "b.txt"])

    def test_dot_names_and_backslashes_are_made_safe(self):
        from workspace.imports.importers.files import safe_local_name

        self.assertEqual(safe_local_name("."), "untitled")
        self.assertEqual(safe_local_name(".."), "untitled")
        self.assertEqual(safe_local_name(" .. "), "untitled")
        self.assertEqual(safe_local_name("a\\b"), "a-b")
        self.assertEqual(safe_local_name("x" * 300), "x" * 255)
