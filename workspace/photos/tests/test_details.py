import re
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.photos.models import MediaItem
from workspace.photos.services.details import photo_details
from workspace.users.services.settings import set_setting

from .images import make_photo, make_video, upload

User = get_user_model()


def _detail_text(html, detail):
    """The visible text of one line of the Photo section, whitespace folded."""
    match = re.search(rf'data-photo-detail="{detail}"\s*>(.*?)</div>', html, re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", match[1]).split())


def _details(**fields):
    return photo_details(MediaItem(**fields))


class PhotoDetailsTests(SimpleTestCase):
    def test_dimensions_and_megapixels(self):
        details = _details(width=4032, height=3024)

        self.assertEqual(details.dimensions, "4032 × 3024")
        self.assertEqual(details.megapixels, "12.2 MP")

    def test_no_megapixels_for_a_thumbnail_sized_picture(self):
        details = _details(width=64, height=48)

        self.assertEqual(details.dimensions, "64 × 48")
        self.assertEqual(details.megapixels, "")

    def test_camera_and_lens_names(self):
        cases = [
            (("Apple", "iPhone 15 Pro"), "Apple iPhone 15 Pro"),
            (("NIKON CORPORATION", "NIKON D850"), "NIKON D850"),
            (("Canon", "Canon EOS R6"), "Canon EOS R6"),
            (("", "EOS R6"), "EOS R6"),
            (("Canon", ""), ""),
            ((None, None), ""),
        ]
        for (make, model), expected in cases:
            with self.subTest(make=make, model=model):
                self.assertEqual(
                    _details(camera_make=make or "", camera_model=model or "").camera,
                    expected,
                )
                self.assertEqual(
                    _details(lens_make=make, lens_model=model).lens, expected
                )

    def test_exposure_line(self):
        details = _details(
            f_number=1.78,
            exposure_time=1 / 120,
            iso=100,
            focal_length=6.765,
            focal_length_35mm=26,
        )

        self.assertEqual(
            details.exposure, ["f/1.8", "1/120 s", "ISO 100", "6.8 mm (26 mm eq.)"]
        )

    def test_shutter_speeds(self):
        cases = {
            1 / 8000: "1/8000 s",
            1 / 120: "1/120 s",
            1 / 4: "1/4 s",
            1 / 3: "0.3 s",
            0.5: "0.5 s",
            2.0: "2 s",
            2.5: "2.5 s",
        }
        for seconds, expected in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(_details(exposure_time=seconds).exposure, [expected])

    def test_focal_lengths(self):
        cases = [
            ((50.0, 50), "50 mm"),
            ((50.0, None), "50 mm"),
            ((35.0, 52), "35 mm (52 mm eq.)"),
            ((None, 26), "26 mm (35 mm eq.)"),
        ]
        for (focal, equivalent), expected in cases:
            with self.subTest(focal=focal, equivalent=equivalent):
                self.assertEqual(
                    _details(focal_length=focal, focal_length_35mm=equivalent).exposure,
                    [expected],
                )

    def test_exposure_bias_and_flash_only_when_they_say_something(self):
        self.assertEqual(
            _details(exposure_bias=-2 / 3, flash_fired=True).exposure,
            ["-0.7 EV", "Flash"],
        )
        self.assertEqual(_details(exposure_bias=0.33).exposure, ["+0.3 EV"])
        self.assertEqual(_details(exposure_bias=0.0, flash_fired=False).exposure, [])

    def test_location(self):
        details = _details(latitude=48.858370, longitude=2.294481, altitude=35.4)

        self.assertEqual(details.coordinates, "48.85837, 2.29448")
        self.assertEqual(details.altitude, "35 m")
        self.assertEqual(
            details.map_url,
            "https://www.openstreetmap.org/?mlat=48.85837&mlon=2.29448"
            "#map=16/48.85837/2.29448",
        )

    def test_no_location(self):
        details = _details(altitude=35.0)

        self.assertEqual((details.coordinates, details.map_url), ("", ""))

    def test_an_empty_row_has_nothing_to_show(self):
        self.assertFalse(_details())
        self.assertTrue(_details(iso=100))


class PhotoPropertiesSectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _panel(self, file_obj):
        response = self.client.get(
            reverse("files_ui:properties", kwargs={"uuid": file_obj.uuid})
        )
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_shows_what_the_photo_says_about_itself(self):
        f = make_photo(
            self.user,
            "beach.jpg",
            datetime(2024, 7, 14, 16, 32, 5, tzinfo=UTC),
            width=4032,
            height=3024,
            camera_make="Apple",
            camera_model="iPhone 15 Pro",
            lens_make="Apple",
            lens_model="iPhone 15 Pro back camera",
            f_number=1.78,
            exposure_time=1 / 120,
            iso=100,
            focal_length_35mm=26,
            latitude=48.8584,
            longitude=2.2945,
        )

        html = self._panel(f)

        self.assertIn('data-properties-section="photo"', html)
        self.assertIn("July 14, 2024, 4:32 p.m.", html)
        self.assertIn("4032 × 3024", html)
        self.assertIn("12.2 MP", html)
        self.assertIn("Apple iPhone 15 Pro", html)
        self.assertIn("Apple iPhone 15 Pro back camera", html)
        self.assertEqual(
            _detail_text(html, "exposure"),
            "f/1.8 · 1/120 s · ISO 100 · 26 mm (35 mm eq.)",
        )
        self.assertIn("48.85840, 2.29450", html)
        self.assertIn(
            'href="https://www.openstreetmap.org/?mlat=48.85840&amp;mlon=2.29450'
            '#map=16/48.85840/2.29450"',
            html,
        )
        self.assertIn('rel="noopener noreferrer"', html)

    def test_capture_date_in_the_viewers_timezone(self):
        set_setting(self.user, "core", "timezone", "Asia/Tokyo")
        f = make_photo(self.user, "a.jpg", datetime(2024, 7, 14, 16, 32, 5, tzinfo=UTC))

        self.assertIn("July 15, 2024, 1:32 a.m.", self._panel(f))

    def test_hidden_without_a_row(self):
        f = upload(self.user, "a.jpg")
        MediaItem.objects.filter(file=f).delete()

        self.assertNotIn('data-properties-section="photo"', self._panel(f))

    def test_hidden_when_the_row_has_nothing_to_show(self):
        f = make_photo(self.user, "undecodable.jpg", None)

        self.assertNotIn('data-properties-section="photo"', self._panel(f))

    def test_hidden_while_the_row_describes_replaced_bytes(self):
        f = make_photo(self.user, "a.jpg", None, iso=100)
        MediaItem.objects.filter(file=f).update(content_hash="0" * 64)

        self.assertNotIn('data-properties-section="photo"', self._panel(f))

    def test_hidden_for_a_video(self):
        f = make_video(
            self.user, "clip.webm", datetime(2024, 7, 14, tzinfo=UTC), width=320
        )

        self.assertNotIn('data-properties-section="photo"', self._panel(f))

    def test_hidden_for_other_files(self):
        f = FileService.create_file(self.user, "notes.txt", acting_user=self.user)

        self.assertNotIn('data-properties-section="photo"', self._panel(f))

    def test_shown_to_someone_the_photo_is_shared_with(self):
        bob = User.objects.create_user(username="bob", password="p")
        f = make_photo(self.user, "a.jpg", None, iso=100)
        share_file(f, target_user=bob, permission="ro", acting_user=self.user)
        self.client.force_login(bob)

        self.assertIn('data-properties-section="photo"', self._panel(f))
