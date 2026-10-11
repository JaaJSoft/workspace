from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase

from workspace.photos.services.filename_dates import date_from_filename

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


def _read(name, tz=PARIS):
    return date_from_filename(name, default_tz=tz, now=NOW)


class DateFromFilenameTests(SimpleTestCase):
    def test_reads_the_names_cameras_and_apps_write(self):
        cases = {
            "IMG_20230415_143022.jpg": datetime(2023, 4, 15, 14, 30, 22),
            "VID_20230415_143022.mp4": datetime(2023, 4, 15, 14, 30, 22),
            "20230415_143022.jpg": datetime(2023, 4, 15, 14, 30, 22),
            "IMG20230415143022.jpg": datetime(2023, 4, 15, 14, 30, 22),
            "WhatsApp Image 2023-04-15 at 14.30.22 (1).jpeg": datetime(
                2023, 4, 15, 14, 30, 22
            ),
            "signal-2023-04-15-143022.jpg": datetime(2023, 4, 15, 14, 30, 22),
            "signal-2023-04-15-14-30-22-123.jpg": datetime(2023, 4, 15, 14, 30, 22),
            "Screenshot_20230415-143022.png": datetime(2023, 4, 15, 14, 30, 22),
            "Screenshot_2023-04-15-14-30-22-123_com.app.jpg": datetime(
                2023, 4, 15, 14, 30, 22
            ),
            "Screenshot 2023-04-15 at 14.30.22.png": datetime(2023, 4, 15, 14, 30, 22),
            "2023-04-15 14.30.22.jpg": datetime(2023, 4, 15, 14, 30, 22),
            "2023-04-15 14h30h22.jpg": datetime(2023, 4, 15, 14, 30, 22),
            "2023_04_15-14_30_22.jpg": datetime(2023, 4, 15, 14, 30, 22),
        }
        for name, wall_clock in cases.items():
            with self.subTest(name=name):
                self.assertEqual(_read(name), wall_clock.replace(tzinfo=PARIS))

    def test_twelve_hour_clock(self):
        cases = {
            "Screen Shot 2020-01-02 at 3.04.05 PM.png": datetime(2020, 1, 2, 15, 4, 5),
            # macOS puts a narrow no-break space before the meridiem.
            "Screen Shot 2020-01-02 at 12.04.05 AM.png": datetime(2020, 1, 2, 0, 4, 5),
            "Screen Shot 2020-01-02 at 12.04.05 PM.png": datetime(2020, 1, 2, 12, 4, 5),
        }
        for name, wall_clock in cases.items():
            with self.subTest(name=name):
                self.assertEqual(_read(name), wall_clock.replace(tzinfo=PARIS))

    def test_a_day_without_a_time_reads_as_noon(self):
        for name in ("IMG-20230415-WA0001.jpg", "2023-04-15 Plage.jpg", "20230415.mp4"):
            with self.subTest(name=name):
                self.assertEqual(_read(name), datetime(2023, 4, 15, 12, tzinfo=PARIS))

    def test_old_scans_named_by_hand(self):
        self.assertEqual(
            _read("1998-07-12 Mariage.jpg"), datetime(1998, 7, 12, 12, tzinfo=PARIS)
        )

    def test_the_time_is_the_owners_wall_clock(self):
        self.assertEqual(
            _read("IMG_20230415_143022.jpg", tz=UTC),
            datetime(2023, 4, 15, 14, 30, 22, tzinfo=UTC),
        )
        self.assertEqual(
            _read("IMG_20230415_143022.jpg"),
            datetime(2023, 4, 15, 12, 30, 22, tzinfo=UTC),
        )

    def test_pixel_names_are_in_utc(self):
        self.assertEqual(
            _read("PXL_20230415_143022123.PORTRAIT.jpg"),
            datetime(2023, 4, 15, 14, 30, 22, tzinfo=UTC),
        )

    def test_an_explicit_utc_marker(self):
        self.assertEqual(
            _read("2023-04-15T14:30:22Z.jpg"),
            datetime(2023, 4, 15, 14, 30, 22, tzinfo=UTC),
        )

    def test_names_without_a_date(self):
        for name in (
            "IMG_1234.jpg",
            "DSC_0001.JPG",
            "P1012345.JPG",
            "5f1e2a3b-90c4.jpg",
            "photo.jpg",
            # Day first: cannot be told apart from month first.
            "15-04-2023.jpg",
            # Longer digit runs are counters or ids, not dates.
            "IMG_120230415.jpg",
        ):
            with self.subTest(name=name):
                self.assertIsNone(_read(name))

    def test_impossible_dates_are_not_read(self):
        for name in (
            "IMG_20231315_000000.jpg",
            "2023-02-30.jpg",
            "IMG_20230415_256000.jpg",
            "Screen Shot 2020-01-02 at 13.04.05 PM.png",
        ):
            with self.subTest(name=name):
                self.assertIsNone(_read(name))

    def test_a_date_in_the_future_is_not_a_capture_date(self):
        self.assertIsNone(_read("roadmap-2027-01-01.jpg"))

    def test_the_first_valid_date_wins(self):
        self.assertEqual(
            _read("2023-02-30 then 2023-03-01.jpg"),
            datetime(2023, 3, 1, 12, tzinfo=PARIS),
        )
