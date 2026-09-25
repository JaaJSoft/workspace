from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from workspace.files.models import File, FileShare
from workspace.photos.queries import library_files
from workspace.photos.services.timeline import (
    START,
    Position,
    encode_cursor,
    mark_favorite_toggles,
    parse_cursor,
    parse_date_position,
    timeline_page,
    with_timeline_fields,
    year_counts,
)

from .images import make_photo

User = get_user_model()
PARIS = ZoneInfo("Europe/Paris")


def _at(*args):
    return datetime(*args, tzinfo=UTC)


def _shape(entries):
    """Entries as short strings: '#2024-07' month, '2024-07-14' day (prefixed
    '+' when it continues a previous page's day) followed by its photos, 'U'
    for the Undated header, or an undated photo's name."""
    out = []
    for entry in entries:
        if entry["kind"] == "month":
            out.append(f"#{entry['date']:%Y-%m}")
        elif entry["kind"] == "day":
            prefix = "+" if entry["continued"] else ""
            out.append(prefix + entry["date"].isoformat())
            out.extend(f.name for f in entry["photos"])
        elif entry["kind"] == "undated":
            out.append("U")
        else:
            out.append(entry["file"].name)
    return out


class TimelineTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def _files(self):
        return with_timeline_fields(library_files(self.user), self.user)

    def _page(self, position=START, tz=UTC, page_size=None):
        return timeline_page(self._files(), position, tz, page_size=page_size)

    def _undated(self, name, created_at):
        f = make_photo(self.user, name, None)
        File.objects.filter(pk=f.pk).update(created_at=created_at)
        return f


class TimelineOrderTests(TimelineTestCase):
    def test_grouped_by_month_then_day_newest_first(self):
        make_photo(self.user, "jul14-morning.jpg", _at(2024, 7, 14, 9))
        make_photo(self.user, "jul14-evening.jpg", _at(2024, 7, 14, 20))
        make_photo(self.user, "jul02.jpg", _at(2024, 7, 2, 12))
        make_photo(self.user, "jun30.jpg", _at(2024, 6, 30, 12))

        page = self._page()

        self.assertEqual(
            _shape(page.entries),
            [
                "#2024-07",
                "2024-07-14",
                "jul14-evening.jpg",
                "jul14-morning.jpg",
                "2024-07-02",
                "jul02.jpg",
                "#2024-06",
                "2024-06-30",
                "jun30.jpg",
            ],
        )
        self.assertIsNone(page.next_cursor)

    def test_undated_bucket_comes_last_by_upload_date(self):
        make_photo(self.user, "dated.jpg", _at(2020, 1, 1, 12))
        self._undated("older-upload.jpg", _at(2025, 1, 1))
        self._undated("newer-upload.jpg", _at(2025, 6, 1))

        self.assertEqual(
            _shape(self._page().entries),
            [
                "#2020-01",
                "2020-01-01",
                "dated.jpg",
                "U",
                "newer-upload.jpg",
                "older-upload.jpg",
            ],
        )

    def test_days_are_the_viewers_local_days(self):
        # 22:30 UTC on the 14th is 00:30 on the 15th in Paris.
        make_photo(self.user, "late.jpg", _at(2024, 7, 14, 22, 30))

        self.assertEqual(
            _shape(self._page(tz=PARIS).entries), ["#2024-07", "2024-07-15", "late.jpg"]
        )
        self.assertEqual(
            _shape(self._page(tz=UTC).entries), ["#2024-07", "2024-07-14", "late.jpg"]
        )


class TimelinePaginationTests(TimelineTestCase):
    def _walk(self, page_size):
        """Every page's shape, following next_cursor to the end."""
        pages = []
        position = START
        while True:
            page = self._page(position, page_size=page_size)
            pages.append(_shape(page.entries))
            if page.next_cursor is None:
                return pages
            position = parse_cursor(page.next_cursor)

    def test_a_page_runs_on_to_the_end_of_its_last_day(self):
        for hour in (9, 12, 15):
            make_photo(self.user, f"jul14-{hour}.jpg", _at(2024, 7, 14, hour))
        make_photo(self.user, "jul13.jpg", _at(2024, 7, 13, 12))

        self.assertEqual(
            self._walk(page_size=2),
            [
                [
                    "#2024-07",
                    "2024-07-14",
                    "jul14-15.jpg",
                    "jul14-12.jpg",
                    "jul14-9.jpg",
                ],
                ["2024-07-13", "jul13.jpg"],
            ],
        )

    def test_the_day_is_taken_in_the_viewers_timezone(self):
        # 22:30 UTC on the 13th is already the 14th in Paris.
        make_photo(self.user, "a.jpg", _at(2024, 7, 14, 12))
        make_photo(self.user, "b.jpg", _at(2024, 7, 13, 22, 30))

        page = self._page(tz=PARIS, page_size=1)

        self.assertEqual(
            _shape(page.entries), ["#2024-07", "2024-07-14", "a.jpg", "b.jpg"]
        )
        self.assertIsNone(page.next_cursor)

    def test_a_day_past_the_overflow_is_cut_and_continues_unlabelled(self):
        for hour in (6, 9, 12, 15):
            make_photo(self.user, f"jul14-{hour}.jpg", _at(2024, 7, 14, hour))
        make_photo(self.user, "jul13.jpg", _at(2024, 7, 13, 12))

        with patch("workspace.photos.services.timeline.DAY_OVERFLOW", 1):
            pages = self._walk(page_size=2)

        self.assertEqual(
            pages,
            [
                [
                    "#2024-07",
                    "2024-07-14",
                    "jul14-15.jpg",
                    "jul14-12.jpg",
                    "jul14-9.jpg",
                ],
                ["+2024-07-14", "jul14-6.jpg", "2024-07-13", "jul13.jpg"],
            ],
        )

    def test_the_undated_header_is_not_repeated(self):
        make_photo(self.user, "dated.jpg", _at(2020, 1, 1, 12))
        for day in (1, 2, 3):
            self._undated(f"u{day}.jpg", _at(2025, 1, day))

        self.assertEqual(
            self._walk(page_size=2),
            [
                ["#2020-01", "2020-01-01", "dated.jpg", "U", "u3.jpg"],
                ["u2.jpg", "u1.jpg"],
            ],
        )

    def test_a_page_ending_on_the_last_dated_photo_leads_to_the_undated_bucket(self):
        make_photo(self.user, "a.jpg", _at(2020, 1, 2, 12))
        make_photo(self.user, "b.jpg", _at(2020, 1, 1, 12))
        self._undated("u.jpg", _at(2025, 1, 1))

        self.assertEqual(
            self._walk(page_size=2),
            [
                ["#2020-01", "2020-01-02", "a.jpg", "2020-01-01", "b.jpg"],
                ["U", "u.jpg"],
            ],
        )

    def test_same_instant_photos_are_neither_repeated_nor_skipped(self):
        burst = [
            make_photo(self.user, f"burst{i}.jpg", _at(2024, 7, 14, 12))
            for i in range(5)
        ]

        with patch("workspace.photos.services.timeline.DAY_OVERFLOW", 1):
            seen = [
                name
                for page in self._walk(page_size=2)
                for name in page
                if name.endswith(".jpg")
            ]

        self.assertEqual(sorted(seen), sorted(f.name for f in burst))

    def test_an_upload_during_scrolling_does_not_shift_the_next_page(self):
        for day in (14, 13, 12):
            make_photo(self.user, f"d{day}.jpg", _at(2024, 7, day, 12))
        first = self._page(page_size=1)

        make_photo(self.user, "new.jpg", _at(2024, 7, 15, 12))
        second = self._page(parse_cursor(first.next_cursor), page_size=1)

        self.assertEqual(_shape(second.entries), ["2024-07-13", "d13.jpg"])

    def test_an_exact_last_page_has_no_cursor(self):
        make_photo(self.user, "a.jpg", _at(2024, 7, 14, 12))
        make_photo(self.user, "b.jpg", _at(2024, 7, 13, 12))

        self.assertIsNone(self._page(page_size=2).next_cursor)

    def test_empty_library(self):
        page = self._page()

        self.assertEqual((page.entries, page.next_cursor), ([], None))


class DatePositionTests(TimelineTestCase):
    def setUp(self):
        super().setUp()
        make_photo(self.user, "aug.jpg", _at(2024, 8, 1, 12))
        make_photo(self.user, "jul14.jpg", _at(2024, 7, 14, 12))
        make_photo(self.user, "jul02.jpg", _at(2024, 7, 2, 12))
        make_photo(self.user, "2023.jpg", _at(2023, 5, 5, 12))
        self._undated("undated.jpg", _at(2025, 1, 1))

    def _names(self, raw):
        page = self._page(parse_date_position(raw, UTC))
        return [n for n in _shape(page.entries) if n.endswith(".jpg")]

    def test_a_day_opens_on_that_day(self):
        self.assertEqual(
            self._names("2024-07-14"),
            ["jul14.jpg", "jul02.jpg", "2023.jpg", "undated.jpg"],
        )

    def test_a_month_opens_on_that_month(self):
        self.assertEqual(
            self._names("2024-07"),
            ["jul14.jpg", "jul02.jpg", "2023.jpg", "undated.jpg"],
        )

    def test_a_year_opens_on_that_year(self):
        self.assertEqual(self._names("2023"), ["2023.jpg", "undated.jpg"])

    def test_undated_opens_the_undated_bucket(self):
        self.assertEqual(self._names("undated"), ["undated.jpg"])

    def test_a_date_opens_with_its_headers(self):
        page = self._page(parse_date_position("2024-07-02", UTC))

        self.assertEqual(
            _shape(page.entries)[:3], ["#2024-07", "2024-07-02", "jul02.jpg"]
        )


class YearCountsTests(TimelineTestCase):
    def test_years_newest_first_then_undated(self):
        make_photo(self.user, "a.jpg", _at(2024, 7, 14, 12))
        make_photo(self.user, "b.jpg", _at(2024, 1, 1, 12))
        make_photo(self.user, "c.jpg", _at(2022, 1, 1, 12))
        self._undated("d.jpg", _at(2025, 1, 1))

        self.assertEqual(
            year_counts(library_files(self.user), UTC),
            [(2024, 2), (2022, 1), (None, 1)],
        )

    def test_years_are_local(self):
        # 23:30 UTC on New Year's Eve is already next year in Paris.
        make_photo(self.user, "a.jpg", _at(2023, 12, 31, 23, 30))

        self.assertEqual(year_counts(library_files(self.user), PARIS), [(2024, 1)])


class CursorTests(SimpleTestCase):
    def test_round_trip(self):
        class _MediaItem:
            taken_at = _at(2024, 7, 14, 12, 30, 1, 123456)

        class _File:
            media_item = _MediaItem()
            uuid = uuid4()
            created_at = _at(2025, 1, 1)

        position = parse_cursor(encode_cursor(_File()))

        self.assertEqual(
            position,
            Position(undated=False, before=_MediaItem.taken_at, after_uuid=_File.uuid),
        )

    def test_undated_cursor_uses_the_upload_date(self):
        class _MediaItem:
            taken_at = None

        class _File:
            media_item = _MediaItem()
            uuid = uuid4()
            created_at = _at(2025, 1, 1, 8)

        position = parse_cursor(encode_cursor(_File()))

        self.assertTrue(position.undated)
        self.assertEqual(position.before, _File.created_at)

    def test_malformed(self):
        for raw in (
            "",
            "garbage",
            "x123.00000000-0000-0000-0000-000000000000",
            "d123.not-a-uuid-at-all-but-36-chars-long",
            "d99999999999999999999.00000000-0000-0000-0000-000000000000",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_cursor(raw)


class ParseDatePositionTests(SimpleTestCase):
    def test_bounds(self):
        cases = {
            "2024-07-14": date(2024, 7, 15),
            "2024-02-29": date(2024, 3, 1),
            "2024-07": date(2024, 8, 1),
            "2024-12": date(2025, 1, 1),
            "2024": date(2025, 1, 1),
        }
        for raw, end in cases.items():
            with self.subTest(raw=raw):
                position = parse_date_position(raw, PARIS)
                self.assertEqual(
                    position.before,
                    datetime(end.year, end.month, end.day, tzinfo=PARIS),
                )
                self.assertFalse(position.undated)
                self.assertFalse(position.continues_page)

    def test_undated(self):
        self.assertEqual(parse_date_position("undated", UTC), Position(undated=True))

    def test_malformed(self):
        for raw in (
            "",
            "yesterday",
            "2024-13",
            "2023-02-29",
            "24-07-14",
            "9999-12-31",
            "0000",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    parse_date_position(raw, UTC)

    def test_day_bound_is_midnight_after_the_day(self):
        position = parse_date_position("2024-07-14", UTC)

        self.assertEqual(position.before - timedelta(days=1), _at(2024, 7, 14))


class FavoriteToggleTests(TimelineTestCase):
    def test_the_registry_decides_who_may_star_a_photo(self):
        mine = make_photo(self.user, "mine.jpg", _at(2024, 7, 14, 12))
        stranger = User.objects.create_user(username="stranger", password="p")
        theirs = make_photo(stranger, "theirs.jpg", _at(2024, 7, 14, 12))

        mark_favorite_toggles([mine, theirs], self.user)

        self.assertTrue(mine.can_favorite)
        self.assertFalse(theirs.can_favorite)

    def test_permissions_are_read_once_for_the_page(self):
        bob = User.objects.create_user(username="bob", password="p")
        shared = []
        for i in range(5):
            f = make_photo(bob, f"s{i}.jpg", _at(2024, 7, 14, 12))
            FileShare.objects.create(
                file=f,
                shared_by=bob,
                shared_with=self.user,
                permission=FileShare.Permission.READ_ONLY,
            )
            shared.append(f)

        with CaptureQueriesContext(connection) as one:
            mark_favorite_toggles(shared[:1], self.user)
        with CaptureQueriesContext(connection) as five:
            mark_favorite_toggles(shared, self.user)

        self.assertEqual(len(five), len(one))
        self.assertTrue(all(f.can_favorite for f in shared))
