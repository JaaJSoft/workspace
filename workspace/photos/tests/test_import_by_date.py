from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.common.tests.media import IsolatedMediaRootMixin
from workspace.files.models import File
from workspace.files.services import FileService
from workspace.photos.models import MediaItem
from workspace.photos.services.import_by_date import file_by_date
from workspace.photos.services.import_folder import (
    choose_import_folder,
    ensure_import_folder,
)
from workspace.photos.tasks import file_imports_by_date
from workspace.users.services.settings import set_setting

from .images import jpeg_bytes, make_photo, png_bytes, upload

User = get_user_model()

API = "/api/v1/photos/import-folder/by-date"


def _at(*args):
    return datetime(*args, tzinfo=UTC)


# Isolated: the folders these tests create stay on storage after the
# rollback, and a later test would find its year folder already there.
class ImportByDateTestCase(IsolatedMediaRootMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)
        set_setting(self.user, "photos", "import_by_date", True)
        self.folder = ensure_import_folder(self.user)

    def tearDown(self):
        cache.clear()

    def _path(self, file_obj):
        file_obj.refresh_from_db()
        return file_obj.path or file_obj.get_path()


class FileByDateTests(ImportByDateTestCase):
    def test_files_a_photo_under_its_year_and_month(self):
        photo = make_photo(
            self.user, "beach.jpg", _at(2024, 7, 14, 12), parent=self.folder
        )

        self.assertEqual(file_by_date(self.user, [photo.uuid]), 1)

        self.assertEqual(self._path(photo), "Pictures/2024/07/beach.jpg")

    def test_the_month_is_the_one_of_the_owners_timezone(self):
        """23:30 UTC on 31 July is already August in Paris."""
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        photo = make_photo(
            self.user, "night.jpg", _at(2024, 7, 31, 23, 30), parent=self.folder
        )

        file_by_date(self.user, [photo.uuid])

        self.assertEqual(self._path(photo), "Pictures/2024/08/night.jpg")

    def test_reuses_the_year_and_month_folders(self):
        first = make_photo(self.user, "a.jpg", _at(2024, 7, 14), parent=self.folder)
        second = make_photo(self.user, "b.jpg", _at(2024, 7, 20), parent=self.folder)
        third = make_photo(self.user, "c.jpg", _at(2024, 3, 2), parent=self.folder)

        file_by_date(self.user, [first.uuid, second.uuid, third.uuid])

        years = File.objects.filter(parent=self.folder, node_type="folder")
        self.assertEqual([f.name for f in years], ["2024"])
        months = File.objects.filter(parent=years[0], node_type="folder")
        self.assertEqual(sorted(f.name for f in months), ["03", "07"])

    def test_a_name_already_in_the_month_keeps_both(self):
        first = make_photo(
            self.user, "IMG_0001.jpg", _at(2024, 7, 14), parent=self.folder
        )
        file_by_date(self.user, [first.uuid])
        second = make_photo(
            self.user, "IMG_0001.jpg", _at(2024, 7, 15), parent=self.folder
        )

        file_by_date(self.user, [second.uuid])

        self.assertEqual(self._path(first), "Pictures/2024/07/IMG_0001.jpg")
        self.assertTrue(self._path(second).startswith("Pictures/2024/07/IMG_0001"))
        self.assertNotEqual(self._path(second), self._path(first))
        second.refresh_from_db()
        with second.content.open("rb") as moved:
            self.assertEqual(moved.read()[:2], bytes([0xFF, 0xD8]))

    def test_an_undated_photo_stays_in_the_import_folder(self):
        scan = make_photo(self.user, "scan.jpg", None, parent=self.folder)

        self.assertEqual(file_by_date(self.user, [scan.uuid]), 0)

        self.assertEqual(self._path(scan), "Pictures/scan.jpg")

    def test_reads_the_date_when_the_analysis_has_not_run_yet(self):
        photo = upload(
            self.user,
            "fresh.jpg",
            jpeg_bytes(taken="2023:12:25 10:00:00"),
            parent=self.folder,
        )
        MediaItem.objects.filter(file=photo).delete()

        file_by_date(self.user, [photo.uuid])

        self.assertEqual(self._path(photo), "Pictures/2023/12/fresh.jpg")
        self.assertTrue(MediaItem.objects.filter(file=photo).exists())

    def test_leaves_what_is_not_at_the_top_of_the_import_folder(self):
        elsewhere = FileService.create_folder(self.user, "Elsewhere")
        outside = make_photo(
            self.user, "outside.jpg", _at(2024, 7, 14), parent=elsewhere
        )
        bob = User.objects.create_user(username="bob", password="p")
        theirs = make_photo(bob, "theirs.jpg", _at(2024, 7, 14))

        self.assertEqual(file_by_date(self.user, [outside.uuid, theirs.uuid]), 0)

        self.assertEqual(self._path(outside), "Elsewhere/outside.jpg")
        self.assertEqual(self._path(theirs), "theirs.jpg")

    def test_nothing_moves_with_the_preference_off(self):
        set_setting(self.user, "photos", "import_by_date", False)
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), parent=self.folder)

        self.assertEqual(file_by_date(self.user, [photo.uuid]), 0)

        self.assertEqual(self._path(photo), "Pictures/a.jpg")

    def test_a_file_named_like_the_year_folder_keeps_the_photo_in_place(self):
        upload(self.user, "2024", png_bytes(), parent=self.folder)
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), parent=self.folder)

        self.assertEqual(file_by_date(self.user, [photo.uuid]), 0)

        self.assertEqual(self._path(photo), "Pictures/a.jpg")

    def test_a_group_import_folder_gets_group_folders(self):
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        root = FileService.create_folder(self.user, "Family", group=family)
        choose_import_folder(self.user, root)
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), parent=root)

        file_by_date(self.user, [photo.uuid])

        photo.refresh_from_db()
        self.assertEqual(photo.parent.name, "07")
        self.assertEqual(photo.parent.group, family)
        self.assertEqual(photo.parent.parent.group, family)

    def test_the_task_files_for_its_user(self):
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), parent=self.folder)

        self.assertEqual(file_imports_by_date(self.user.pk, [str(photo.uuid)]), 1)
        self.assertEqual(file_imports_by_date(987654, [str(photo.uuid)]), 0)


class ImportByDateApiTests(ImportByDateTestCase):
    def test_queues_the_files_at_the_top_of_the_import_folder(self):
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), parent=self.folder)
        elsewhere = make_photo(self.user, "b.jpg", _at(2024, 7, 14))

        with (
            patch("workspace.photos.views.import_folder.file_imports_by_date") as task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                API,
                {"files": [str(photo.uuid), str(elsewhere.uuid)]},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"queued": 1})
        task.delay.assert_called_once_with(self.user.pk, [str(photo.uuid)])

    def test_queues_nothing_with_the_preference_off(self):
        set_setting(self.user, "photos", "import_by_date", False)
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14), parent=self.folder)

        with (
            patch("workspace.photos.views.import_folder.file_imports_by_date") as task,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                API, {"files": [str(photo.uuid)]}, content_type="application/json"
            )

        self.assertEqual(response.json(), {"queued": 0})
        task.delay.assert_not_called()

    def test_rejects_anything_but_a_list_of_uuids(self):
        for body in (
            {},
            {"files": []},
            {"files": "abc"},
            {"files": ["not-a-uuid"]},
            {"files": [str(self.folder.uuid)] * 501},
        ):
            with self.subTest(body=str(body)[:40]):
                response = self.client.post(API, body, content_type="application/json")
                self.assertEqual(response.status_code, 400)

    def test_login_required(self):
        self.client.logout()

        response = self.client.post(API, {"files": []}, content_type="application/json")

        self.assertIn(response.status_code, (401, 403))
