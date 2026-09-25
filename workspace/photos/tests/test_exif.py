import io
from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase
from PIL import ExifTags, UnidentifiedImageError
from PIL.TiffImagePlugin import IFDRational

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


def _settings(**tags):
    """Read a JPEG whose Exif IFD carries *tags*, keyed by ExifTags.Base name."""
    return _read(
        jpeg_bytes(
            exif_tags={ExifTags.Base[name]: value for name, value in tags.items()}
        )
    )


def _gps(**tags):
    """Read a JPEG whose GPS IFD carries *tags*, keyed by ExifTags.GPS name."""
    return _read(
        jpeg_bytes(gps={ExifTags.GPS[name]: value for name, value in tags.items()})
    )


class LensTests(SimpleTestCase):
    def test_lens_make_and_model(self):
        meta = _settings(
            LensMake="Apple\x00", LensModel="iPhone 15 Pro back camera 6.765mm f/1.78"
        )

        self.assertEqual(meta.lens_make, "Apple")
        self.assertEqual(meta.lens_model, "iPhone 15 Pro back camera 6.765mm f/1.78")

    def test_no_lens_tags(self):
        meta = _read(jpeg_bytes())

        self.assertEqual((meta.lens_make, meta.lens_model), ("", ""))


class ExposureSettingsTests(SimpleTestCase):
    def test_the_usual_settings(self):
        meta = _settings(
            FNumber=IFDRational(178, 100),
            ExposureTime=IFDRational(1, 120),
            ISOSpeedRatings=100,
            FocalLength=IFDRational(6765, 1000),
            FocalLengthIn35mmFilm=26,
            ExposureBiasValue=IFDRational(-2, 3),
        )

        self.assertAlmostEqual(meta.f_number, 1.78)
        self.assertAlmostEqual(meta.exposure_time, 1 / 120)
        self.assertEqual(meta.iso, 100)
        self.assertAlmostEqual(meta.focal_length, 6.765)
        self.assertEqual(meta.focal_length_35mm, 26)
        self.assertAlmostEqual(meta.exposure_bias, -2 / 3)

    def test_no_exif_leaves_every_setting_empty(self):
        meta = _read(jpeg_bytes())

        for name in (
            "focal_length",
            "focal_length_35mm",
            "f_number",
            "exposure_time",
            "iso",
            "exposure_bias",
            "flash_fired",
        ):
            with self.subTest(field=name):
                self.assertIsNone(getattr(meta, name))

    def test_iso_written_as_a_list_takes_the_first_value(self):
        self.assertEqual(_settings(ISOSpeedRatings=(400, 800)).iso, 400)

    def test_zero_denominator_rationals_are_dropped(self):
        meta = _settings(
            FNumber=IFDRational(1, 0),
            ExposureTime=IFDRational(0, 0),
            FocalLength=IFDRational(35, 0),
            ExposureBiasValue=IFDRational(1, 0),
        )

        self.assertIsNone(meta.f_number)
        self.assertIsNone(meta.exposure_time)
        self.assertIsNone(meta.focal_length)
        self.assertIsNone(meta.exposure_bias)

    def test_out_of_range_values_are_dropped(self):
        meta = _settings(
            FNumber=0.0,
            ExposureTime=0.0,
            ISOSpeedRatings=0,
            FocalLength=IFDRational(99_999, 1),
            ExposureBiasValue=IFDRational(-500, 1),
        )

        self.assertIsNone(meta.f_number)
        self.assertIsNone(meta.exposure_time)
        self.assertIsNone(meta.iso)
        self.assertIsNone(meta.focal_length)
        self.assertIsNone(meta.exposure_bias)

    def test_text_where_a_number_belongs_is_dropped(self):
        meta = _settings(FNumber="f/2.8", ExposureTime="1/60", LensModel=1234)

        self.assertIsNone(meta.f_number)
        self.assertIsNone(meta.exposure_time)
        self.assertEqual(meta.lens_model, "")

    def test_flash(self):
        cases = {
            0x00: False,  # did not fire
            0x10: False,  # did not fire, compulsory suppression
            0x01: True,  # fired
            0x19: True,  # fired, auto mode
            0x20: None,  # the camera has no flash
        }
        for value, fired in cases.items():
            with self.subTest(flash=hex(value)):
                self.assertIs(_settings(Flash=value).flash_fired, fired)


class GpsTests(SimpleTestCase):
    def test_degrees_minutes_seconds_to_signed_decimal_degrees(self):
        meta = _gps(
            GPSLatitudeRef="N",
            GPSLatitude=(48.0, 51.0, 30.24),
            GPSLongitudeRef="E",
            GPSLongitude=(2.0, 17.0, 40.2),
        )

        self.assertAlmostEqual(meta.latitude, 48.8584)
        self.assertAlmostEqual(meta.longitude, 2.2945)

    def test_south_and_west_are_negative(self):
        meta = _gps(
            GPSLatitudeRef="S",
            GPSLatitude=(33.0, 51.0, 36.0),
            GPSLongitudeRef="W",
            GPSLongitude=(70.0, 30.0, 0.0),
        )

        self.assertAlmostEqual(meta.latitude, -33.86)
        self.assertAlmostEqual(meta.longitude, -70.5)

    def test_decimal_minutes_without_seconds(self):
        meta = _gps(
            GPSLatitudeRef="N",
            GPSLatitude=(48.0, 51.504),
            GPSLongitudeRef="E",
            GPSLongitude=(2.0, 17.67),
        )

        self.assertAlmostEqual(meta.latitude, 48.8584)
        self.assertAlmostEqual(meta.longitude, 2.2945)

    def test_missing_refs_keep_the_value_as_is(self):
        meta = _gps(GPSLatitude=(10.0, 30.0, 0.0), GPSLongitude=(20.0, 0.0, 0.0))

        self.assertAlmostEqual(meta.latitude, 10.5)
        self.assertAlmostEqual(meta.longitude, 20.0)

    def test_padded_refs_are_read(self):
        meta = _gps(
            GPSLatitudeRef="S\x00",
            GPSLatitude=(10.0, 0.0, 0.0),
            GPSLongitudeRef="w",
            GPSLongitude=(20.0, 0.0, 0.0),
        )

        self.assertAlmostEqual(meta.latitude, -10.0)
        self.assertAlmostEqual(meta.longitude, -20.0)

    def test_altitude(self):
        above = _gps(
            GPSLatitude=(10.0, 0.0, 0.0),
            GPSLongitude=(20.0, 0.0, 0.0),
            GPSAltitude=IFDRational(35, 1),
            GPSAltitudeRef=b"\x00",
        )
        below = _gps(
            GPSLatitude=(31.0, 30.0, 0.0),
            GPSLongitude=(35.0, 30.0, 0.0),
            GPSAltitude=IFDRational(430, 1),
            GPSAltitudeRef=b"\x01",
        )

        self.assertAlmostEqual(above.altitude, 35.0)
        self.assertAlmostEqual(below.altitude, -430.0)

    def test_the_no_fix_placeholder_is_dropped(self):
        meta = _gps(
            GPSLatitudeRef="N",
            GPSLatitude=(0.0, 0.0, 0.0),
            GPSLongitudeRef="E",
            GPSLongitude=(0.0, 0.0, 0.0),
            GPSAltitude=IFDRational(12, 1),
        )

        self.assertEqual((meta.latitude, meta.longitude, meta.altitude), (None,) * 3)

    def test_unusable_positions_are_dropped(self):
        cases = {
            "latitude out of range": {
                "GPSLatitude": (91.0, 0.0, 0.0),
                "GPSLongitude": (20.0, 0.0, 0.0),
            },
            "longitude out of range": {
                "GPSLatitude": (10.0, 0.0, 0.0),
                "GPSLongitude": (179.0, 60.0, 1.0),
            },
            "longitude missing": {"GPSLatitude": (10.0, 0.0, 0.0)},
            "unknown ref": {
                "GPSLatitudeRef": "X",
                "GPSLatitude": (10.0, 0.0, 0.0),
                "GPSLongitude": (20.0, 0.0, 0.0),
            },
            "zero denominator": {
                "GPSLatitude": (IFDRational(10, 0), 0.0, 0.0),
                "GPSLongitude": (20.0, 0.0, 0.0),
            },
        }
        for label, tags in cases.items():
            with self.subTest(label):
                meta = _gps(**tags, GPSAltitude=IFDRational(35, 1))
                self.assertEqual(
                    (meta.latitude, meta.longitude, meta.altitude), (None,) * 3
                )

    def test_no_gps(self):
        meta = _read(jpeg_bytes())

        self.assertEqual((meta.latitude, meta.longitude, meta.altitude), (None,) * 3)
