from datetime import UTC, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.core.module_registry import registry
from workspace.core.services.search import search_modules
from workspace.files.services.search_index import index_file
from workspace.photos.search import search_photos
from workspace.users.services.settings import set_setting

from .images import make_photo as _make_photo
from .images import upload


def make_photo(*args, **kwargs):
    # The index is written by a task on commit, which a TestCase never reaches.
    file_obj = _make_photo(*args, **kwargs)
    index_file(file_obj)
    return file_obj


User = get_user_model()


class SearchPhotosTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def tearDown(self):
        cache.clear()

    def test_hit_opens_the_timeline_on_the_capture_day(self):
        f = make_photo(
            self.user, "sunset-beach.jpg", datetime(2024, 7, 14, 20, tzinfo=UTC)
        )

        [hit] = search_photos("sunset", self.user, 10)

        self.assertEqual(hit.url, f"/photos?date=2024-07-14&open={f.uuid}")
        self.assertEqual(hit.module_slug, "photos")
        self.assertEqual(hit.date, "14 Jul 2024")

    def test_capture_day_is_the_users_local_day(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        f = make_photo(self.user, "late.jpg", datetime(2024, 7, 14, 22, 30, tzinfo=UTC))

        [hit] = search_photos("late", self.user, 10)

        self.assertEqual(hit.url, f"/photos?date=2024-07-15&open={f.uuid}")

    def test_undated_hit_opens_the_undated_bucket(self):
        f = make_photo(self.user, "receipt-scan.png", None)

        [hit] = search_photos("receipt", self.user, 10)

        self.assertEqual(hit.url, f"/photos?date=undated&open={f.uuid}")
        self.assertIsNone(hit.date)

    def test_only_the_users_analyzed_photos(self):
        bob = User.objects.create_user(username="bob", password="p")
        make_photo(bob, "sunset-bob.jpg", datetime(2024, 7, 14, tzinfo=UTC))
        index_file(upload(self.user, "sunset-notes.txt", b"sunset"))
        index_file(upload(self.user, "sunset-fresh.jpg"))

        self.assertEqual(search_photos("sunset", self.user, 10), [])


class UnifiedSearchTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.photo = make_photo(
            self.user, "sunset-beach.jpg", datetime(2024, 7, 14, 20, tzinfo=UTC)
        )

    @patch("workspace.core.services.search.is_module_slug_visible", return_value=True)
    def test_the_photos_row_supersedes_the_files_row(self, _visible):
        hits = [
            h
            for h in search_modules("sunset", self.user)
            if h["uuid"] == str(self.photo.uuid)
        ]

        self.assertEqual([h["provider_slug"] for h in hits], ["photos"])

    @patch("workspace.core.services.search.is_module_slug_visible")
    def test_a_user_without_the_module_still_gets_the_files_row(self, visible):
        visible.side_effect = lambda user, slug: slug != "photos"

        hits = [
            h
            for h in search_modules("sunset", self.user)
            if h["uuid"] == str(self.photo.uuid)
        ]

        self.assertEqual([h["provider_slug"] for h in hits], ["files"])


class RegistrationTests(TestCase):
    def test_module_is_a_preview(self):
        module = registry.get("photos")

        self.assertTrue(module.preview)
        self.assertEqual(module.url, "/photos")

    def test_search_provider_refines_files(self):
        self.assertIn("files", registry.refinements().get("photos", ()))

    def test_palette_commands(self):
        urls = {
            c.url for c in registry.get_active_commands() if c.module_slug == "photos"
        }

        self.assertEqual(
            urls, {"/photos", "/photos?favorites=1", "/photos?date=undated"}
        )
