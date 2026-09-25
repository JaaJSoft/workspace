import io
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from PIL import UnidentifiedImageError

from workspace.photos.services.exif import CAMERA_FIELD_LENGTH, read_metadata

from .images import jpeg_bytes, png_bytes

PARIS = ZoneInfo("Europe/Paris")


def _read(data, **kwargs):
    return read_metadata(io.BytesIO(data), **kwargs)


class CaptureTimeTests(SimpleTestCase):
    def test_offset_makes_the_exact_instant(self):
        meta = _read(jpeg_bytes(taken="2024:07:14 18:32:05", offset="+02:00"))

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 16, 32, 5, tzinfo=UTC))
        self.assertEqual(meta.taken_at.utcoffset(), timedelta(hours=2))

    def test_negative_offset(self):
        meta = _read(jpeg_bytes(taken="2024:01:02 03:04:05", offset="-05:30"))

        self.assertEqual(meta.taken_at, datetime(2024, 1, 2, 8, 34, 5, tzinfo=UTC))

    def test_wall_clock_without_offset_is_read_in_the_default_timezone(self):
        meta = _read(jpeg_bytes(taken="2024:07:14 18:32:05"), default_tz=PARIS)

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 18, 32, 5, tzinfo=PARIS))

    def test_default_timezone_defaults_to_utc(self):
        meta = _read(jpeg_bytes(taken="2024:07:14 18:32:05"))

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 18, 32, 5, tzinfo=UTC))

    def test_malformed_offset_falls_back_to_the_default_timezone(self):
        for offset in ("+25:00", "+02:75", "02:00", "garbage", ""):
            with self.subTest(offset=offset):
                meta = _read(
                    jpeg_bytes(taken="2024:07:14 18:32:05", offset=offset),
                    default_tz=PARIS,
                )
                self.assertEqual(
                    meta.taken_at, datetime(2024, 7, 14, 18, 32, 5, tzinfo=PARIS)
                )

    def test_no_exif_means_no_capture_date(self):
        meta = _read(jpeg_bytes())

        self.assertIsNone(meta.taken_at)

    def test_offset_alone_is_not_a_capture_date(self):
        meta = _read(jpeg_bytes(offset="+02:00"))

        self.assertIsNone(meta.taken_at)

    def test_placeholder_dates_mean_no_capture_date(self):
        for raw in ("0000:00:00 00:00:00", "    :  :     :  :  ", "yesterday"):
            with self.subTest(raw=raw):
                self.assertIsNone(_read(jpeg_bytes(taken=raw)).taken_at)

    def test_dashed_date_separator_is_accepted(self):
        meta = _read(jpeg_bytes(taken="2024-07-14 18:32:05"))

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 18, 32, 5, tzinfo=UTC))

    def test_trailing_nul_is_ignored(self):
        meta = _read(jpeg_bytes(taken="2024:07:14 18:32:05\x00"))

        self.assertEqual(meta.taken_at, datetime(2024, 7, 14, 18, 32, 5, tzinfo=UTC))


class DimensionTests(SimpleTestCase):
    def test_dimensions_as_stored_when_upright(self):
        for orientation in (None, 1, 2, 3, 4):
            with self.subTest(orientation=orientation):
                meta = _read(jpeg_bytes(size=(64, 48), orientation=orientation))
                self.assertEqual((meta.width, meta.height), (64, 48))

    def test_quarter_turn_orientations_swap_width_and_height(self):
        # A phone held upright stores a landscape frame tagged "rotate 90":
        # the picture people see is portrait.
        for orientation in (5, 6, 7, 8):
            with self.subTest(orientation=orientation):
                meta = _read(jpeg_bytes(size=(64, 48), orientation=orientation))
                self.assertEqual((meta.width, meta.height), (48, 64))

    def test_png_without_exif(self):
        meta = _read(png_bytes(size=(20, 10)))

        self.assertEqual((meta.width, meta.height), (20, 10))
        self.assertIsNone(meta.taken_at)
        self.assertEqual((meta.camera_make, meta.camera_model), ("", ""))


class CameraTests(SimpleTestCase):
    def test_make_and_model(self):
        meta = _read(jpeg_bytes(make="Canon", model="EOS R6"))

        self.assertEqual((meta.camera_make, meta.camera_model), ("Canon", "EOS R6"))

    def test_padding_is_stripped(self):
        meta = _read(jpeg_bytes(make="  NIKON CORPORATION\x00\x00", model="Z 6\x00"))

        self.assertEqual(
            (meta.camera_make, meta.camera_model), ("NIKON CORPORATION", "Z 6")
        )

    def test_overlong_values_are_cut_to_the_column(self):
        meta = _read(jpeg_bytes(model="x" * 300))

        self.assertEqual(len(meta.camera_model), CAMERA_FIELD_LENGTH)


class UndecodableTests(SimpleTestCase):
    def test_bytes_that_are_no_image_raise(self):
        with self.assertRaises(UnidentifiedImageError):
            _read(b"definitely not a picture")


class OffsetTimezoneTests(SimpleTestCase):
    def test_offset_is_kept_on_the_value(self):
        meta = _read(jpeg_bytes(taken="2024:07:14 18:32:05", offset="+09:00"))

        self.assertEqual(meta.taken_at.tzinfo, timezone(timedelta(hours=9)))
