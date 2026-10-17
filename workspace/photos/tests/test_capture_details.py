from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from PIL import ExifTags

from workspace.files.models import FileShare
from workspace.files.services import FileService
from workspace.files.services.sharing import share_file
from workspace.photos.models import MediaItem
from workspace.photos.services.analysis import analyze_media, redate_from_name
from workspace.photos.services.capture_details import (
    restore_from_file,
    set_location,
    set_taken_at,
    shift_taken_at,
)
from workspace.photos.tasks import reread_media
from workspace.users.services.settings import set_setting

from .images import jpeg_bytes, make_photo, upload

User = get_user_model()

URL = "/api/v1/photos/capture-details"
PARIS = ZoneInfo("Europe/Paris")


def _at(*args):
    return datetime(*args, tzinfo=UTC)


def _item(file_obj):
    return MediaItem.objects.get(file=file_obj)


def _gps_photo(owner, name="trip.jpg"):
    """A JPEG dated 2024-07-14 12:00 UTC and placed in Paris by its EXIF."""
    return upload(
        owner,
        name,
        jpeg_bytes(
            taken="2024:07:14 14:00:00",
            offset="+02:00",
            gps={
                ExifTags.GPS.GPSLatitudeRef: "N",
                ExifTags.GPS.GPSLatitude: (48.0, 51.0, 30.0),
                ExifTags.GPS.GPSLongitudeRef: "E",
                ExifTags.GPS.GPSLongitude: (2.0, 17.0, 40.0),
                ExifTags.GPS.GPSAltitude: 35.0,
            },
        ),
    )


class ShiftTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_moves_each_date_by_the_shift(self):
        a = make_photo(self.user, "a.jpg", _at(2024, 7, 14, 10))
        b = make_photo(self.user, "b.jpg", _at(2024, 7, 15, 23, 30))

        moved = shift_taken_at([a.pk, b.pk], timedelta(hours=2))

        self.assertEqual(moved, 2)
        self.assertEqual(_item(a).taken_at, _at(2024, 7, 14, 12))
        self.assertEqual(_item(b).taken_at, _at(2024, 7, 16, 1, 30))
        self.assertTrue(_item(a).taken_at_by_user)

    def test_moves_back_too(self):
        a = make_photo(self.user, "a.jpg", _at(2024, 7, 14, 10))

        shift_taken_at([a.pk], timedelta(days=-365))

        self.assertEqual(_item(a).taken_at, _at(2023, 7, 15, 10))

    def test_an_undated_photo_has_nothing_to_move(self):
        scan = make_photo(self.user, "scan.jpg", None)

        self.assertEqual(shift_taken_at([scan.pk], timedelta(hours=2)), 0)
        self.assertIsNone(_item(scan).taken_at)
        self.assertFalse(_item(scan).taken_at_by_user)

    def test_a_shift_past_what_a_database_stores_skips_the_photo(self):
        old = make_photo(self.user, "old.jpg", _at(10, 1, 1))

        self.assertEqual(shift_taken_at([old.pk], timedelta(days=-36500)), 0)
        self.assertEqual(_item(old).taken_at, _at(10, 1, 1))

    def test_a_date_read_from_the_name_becomes_a_date_set_by_hand(self):
        a = make_photo(
            self.user, "a.jpg", _at(2024, 7, 14, 12), taken_at_from_name=True
        )

        shift_taken_at([a.pk], timedelta(hours=1))

        self.assertFalse(_item(a).taken_at_from_name)


class SetDateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_dates_on_the_day_and_time_in_the_timezone(self):
        a = make_photo(self.user, "a.jpg", _at(2024, 7, 14, 10))

        set_taken_at([a.pk], date(1998, 7, 12), time(14, 30), PARIS)

        self.assertEqual(_item(a).taken_at, _at(1998, 7, 12, 12, 30))
        self.assertTrue(_item(a).taken_at_by_user)

    def test_without_a_time_each_photo_keeps_its_own(self):
        # 23:30 and 09:15 in Paris: the order of the batch survives the move.
        late = make_photo(self.user, "late.jpg", _at(2024, 7, 14, 21, 30))
        early = make_photo(self.user, "early.jpg", _at(2024, 3, 2, 8, 15))

        set_taken_at([late.pk, early.pk], date(1998, 7, 12), None, PARIS)

        self.assertEqual(_item(late).taken_at, _at(1998, 7, 12, 21, 30))
        self.assertEqual(_item(early).taken_at, _at(1998, 7, 12, 7, 15))

    def test_an_undated_scan_lands_at_noon(self):
        scan = make_photo(self.user, "scan.jpg", None)

        self.assertEqual(set_taken_at([scan.pk], date(1998, 7, 12), None, PARIS), 1)

        self.assertEqual(_item(scan).taken_at, _at(1998, 7, 12, 10))


class LocationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def test_places_the_photos_and_drops_the_old_altitude(self):
        a = make_photo(
            self.user, "a.jpg", None, latitude=1.0, longitude=2.0, altitude=300.0
        )

        self.assertEqual(set_location([a.pk], 43.2965, 5.3698), 1)

        item = _item(a)
        self.assertEqual((item.latitude, item.longitude), (43.2965, 5.3698))
        self.assertIsNone(item.altitude)
        self.assertTrue(item.location_by_user)

    def test_removing_the_place(self):
        a = make_photo(self.user, "a.jpg", None, latitude=1.0, longitude=2.0)

        set_location([a.pk], None, None)

        item = _item(a)
        self.assertIsNone(item.latitude)
        self.assertIsNone(item.longitude)
        self.assertTrue(item.location_by_user)


class CorrectionsSurviveTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")

    def tearDown(self):
        cache.clear()

    def test_reading_the_file_again_keeps_a_date_and_a_place_set_by_hand(self):
        photo = _gps_photo(self.user)
        analyze_media(photo)
        set_taken_at([photo.pk], date(1998, 7, 12), time(10), UTC)
        set_location([photo.pk], 43.2965, 5.3698)

        item = analyze_media(photo)

        item.refresh_from_db()
        self.assertEqual(item.taken_at, _at(1998, 7, 12, 10))
        self.assertEqual((item.latitude, item.longitude), (43.2965, 5.3698))
        self.assertIsNone(item.altitude)

    def test_reading_the_file_again_keeps_a_removed_place_removed(self):
        photo = _gps_photo(self.user)
        analyze_media(photo)
        set_location([photo.pk], None, None)

        item = analyze_media(photo)

        item.refresh_from_db()
        self.assertIsNone(item.latitude)
        self.assertEqual(item.taken_at, _at(2024, 7, 14, 12))

    def test_a_rename_leaves_a_date_set_by_hand_alone(self):
        scan = upload(self.user, "2001-05-06 scan.jpg")
        analyze_media(scan)
        set_taken_at([scan.pk], date(1998, 7, 12), time(10), UTC)

        scan.name = "2010-01-01 scan.jpg"
        redate_from_name(scan)

        self.assertEqual(_item(scan).taken_at, _at(1998, 7, 12, 10))

    def test_restoring_hands_the_date_back_to_the_file(self):
        photo = _gps_photo(self.user)
        analyze_media(photo)
        shift_taken_at([photo.pk], timedelta(hours=3))
        set_location([photo.pk], None, None)

        with (
            patch.object(reread_media, "delay", side_effect=reread_media),
            self.captureOnCommitCallbacks(execute=True),
        ):
            self.assertEqual(restore_from_file([photo.pk], taken_at=True), 1)

        item = _item(photo)
        self.assertEqual(item.taken_at, _at(2024, 7, 14, 12))
        self.assertFalse(item.taken_at_by_user)
        # Only the date was restored: the place stays removed.
        self.assertIsNone(item.latitude)
        self.assertTrue(item.location_by_user)

    def test_restoring_the_place_reads_it_from_the_file(self):
        photo = _gps_photo(self.user)
        analyze_media(photo)
        set_location([photo.pk], 1.0, 2.0)

        with (
            patch.object(reread_media, "delay", side_effect=reread_media),
            self.captureOnCommitCallbacks(execute=True),
        ):
            restore_from_file([photo.pk], location=True)

        item = _item(photo)
        self.assertAlmostEqual(item.latitude, 48.8583, places=3)
        self.assertEqual(item.altitude, 35.0)
        self.assertFalse(item.location_by_user)

    def test_restoring_what_was_never_corrected_reads_nothing(self):
        photo = make_photo(self.user, "a.jpg", _at(2024, 7, 14))

        with patch.object(reread_media, "delay") as delay:
            self.assertEqual(
                restore_from_file([photo.pk], taken_at=True, location=True), 0
            )
        delay.assert_not_called()


class CaptureDetailsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="p")
        self.bob = User.objects.create_user(username="bob", password="p")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _post(self, body):
        return self.client.post(URL, body, content_type="application/json")

    def test_shifts_the_selection(self):
        a = make_photo(self.user, "a.jpg", _at(2024, 7, 14, 10))
        scan = make_photo(self.user, "scan.jpg", None)

        response = self._post(
            {
                "uuids": [str(a.uuid), str(scan.uuid)],
                "date_mode": "shift",
                "shift_minutes": -120,
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"dated": 1, "located": 0})
        self.assertEqual(_item(a).taken_at, _at(2024, 7, 14, 8))

    def test_sets_a_date_in_the_callers_timezone(self):
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        scan = make_photo(self.user, "scan.jpg", None)

        response = self._post(
            {
                "uuids": [str(scan.uuid)],
                "date_mode": "set",
                "date": "1998-07-12",
                "time": "14:30",
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_item(scan).taken_at, _at(1998, 7, 12, 12, 30))

    def test_sets_the_date_and_the_place_at_once(self):
        a = make_photo(self.user, "a.jpg", None)

        response = self._post(
            {
                "uuids": [str(a.uuid)],
                "date_mode": "set",
                "date": "1998-07-12",
                "location_mode": "set",
                "latitude": 43.2965,
                "longitude": 5.3698,
            }
        )

        self.assertEqual(response.json(), {"dated": 1, "located": 1})
        self.assertEqual(_item(a).latitude, 43.2965)

    def test_removes_the_place(self):
        a = make_photo(self.user, "a.jpg", None, latitude=1.0, longitude=2.0)

        response = self._post({"uuids": [str(a.uuid)], "location_mode": "clear"})

        self.assertEqual(response.json(), {"dated": 0, "located": 1})
        self.assertIsNone(_item(a).latitude)

    def test_refuses_an_empty_correction(self):
        a = make_photo(self.user, "a.jpg", None)

        response = self._post({"uuids": [str(a.uuid)]})

        self.assertEqual(response.status_code, 400)

    def test_refuses_incomplete_or_out_of_range_values(self):
        a = make_photo(self.user, "a.jpg", None)
        uuids = [str(a.uuid)]

        for body in (
            {"date_mode": "shift"},
            {"date_mode": "shift", "shift_minutes": 0},
            {"date_mode": "set"},
            {"location_mode": "set", "latitude": 10},
            {"location_mode": "set", "latitude": 91, "longitude": 0},
            {"location_mode": "set", "latitude": 0, "longitude": 181},
            {"date_mode": "move"},
        ):
            with self.subTest(body=body):
                self.assertEqual(self._post({"uuids": uuids, **body}).status_code, 400)
        self.assertFalse(_item(a).taken_at_by_user)
        self.assertFalse(_item(a).location_by_user)

    def test_refuses_malformed_uuids(self):
        response = self._post({"uuids": ["nope"], "location_mode": "clear"})

        self.assertEqual(response.status_code, 400)

    def test_a_photo_the_caller_cannot_open_is_not_found(self):
        bobs = make_photo(self.bob, "bob.jpg", _at(2024, 7, 14))

        response = self._post({"uuids": [str(bobs.uuid)], "location_mode": "clear"})

        self.assertEqual(response.status_code, 404)

    def test_a_file_outside_the_library_is_not_found(self):
        unanalyzed = upload(self.user, "new.jpg")
        MediaItem.objects.filter(file=unanalyzed).delete()

        response = self._post(
            {"uuids": [str(unanalyzed.uuid)], "location_mode": "clear"}
        )

        self.assertEqual(response.status_code, 404)

    def test_a_photo_shared_read_only_cannot_be_corrected(self):
        mine = make_photo(self.user, "mine.jpg", _at(2024, 7, 14))
        bobs = make_photo(self.bob, "bob.jpg", _at(2024, 7, 14))
        share_file(
            bobs,
            target_user=self.user,
            permission=FileShare.Permission.READ_ONLY,
            acting_user=self.bob,
        )

        response = self._post(
            {
                "uuids": [str(mine.uuid), str(bobs.uuid)],
                "date_mode": "shift",
                "shift_minutes": 60,
            }
        )

        self.assertEqual(response.status_code, 403)
        # Nothing changed: not even the caller's own photo.
        self.assertEqual(_item(mine).taken_at, _at(2024, 7, 14))

    def test_a_photo_shared_read_write_cannot_be_corrected_either(self):
        # Read & write lets the recipient replace the content, not what the
        # owner sees of it.
        bobs = make_photo(self.bob, "bob.jpg", _at(2024, 7, 14))
        share_file(
            bobs,
            target_user=self.user,
            permission=FileShare.Permission.READ_WRITE,
            acting_user=self.bob,
        )

        response = self._post({"uuids": [str(bobs.uuid)], "location_mode": "clear"})

        self.assertEqual(response.status_code, 403)

    def test_a_group_photo_another_member_added_can_be_corrected(self):
        family = Group.objects.create(name="Family")
        self.user.groups.add(family)
        self.bob.groups.add(family)
        root = FileService.create_folder(owner=self.bob, name="Family", group=family)
        picnic = make_photo(self.bob, "picnic.jpg", _at(2024, 7, 14), parent=root)

        response = self._post(
            {"uuids": [str(picnic.uuid)], "date_mode": "shift", "shift_minutes": 60}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(_item(picnic).taken_at, _at(2024, 7, 14, 1))

    def test_a_trashed_photo_cannot_be_corrected(self):
        a = make_photo(self.user, "a.jpg", _at(2024, 7, 14))
        FileService.soft_delete(a, acting_user=self.user)

        response = self._post({"uuids": [str(a.uuid)], "location_mode": "clear"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(_item(a).location_by_user)
