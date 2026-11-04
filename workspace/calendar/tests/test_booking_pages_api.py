from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Booking, BookingPage, Calendar, Event
from workspace.calendar.models_external import ExternalCalendar
from workspace.mail.models import MailAccount
from workspace.users.services.settings import set_setting

User = get_user_model()

LIST_URL = "/api/v1/booking-pages"


def mail_account(owner, email):
    return MailAccount.objects.create(
        owner=owner,
        email=email,
        imap_host="imap.test",
        smtp_host="smtp.test",
        username=email,
    )


class BookingPageApiTestMixin:
    def setUp(self):
        self.owner = User.objects.create_user(username="host", password="pw")
        self.other = User.objects.create_user(username="other", password="pw")
        self.calendar = Calendar.objects.create(name="Work", owner=self.owner)
        self.others_calendar = Calendar.objects.create(name="Theirs", owner=self.other)
        self.client.force_authenticate(self.owner)

    def tearDown(self):
        cache.clear()

    def create(self, **overrides):
        payload = {"title": "Intro call", "calendar": str(self.calendar.uuid)}
        payload.update(overrides)
        return self.client.post(LIST_URL, payload, format="json")


class BookingPageCreateTests(BookingPageApiTestMixin, APITestCase):
    def test_create_with_defaults(self):
        set_setting(self.owner, "core", "timezone", "Asia/Tokyo")

        resp = self.create()

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        page = BookingPage.objects.get(uuid=resp.data["uuid"])
        self.assertEqual(page.owner, self.owner)
        self.assertEqual(page.timezone, "Asia/Tokyo")
        self.assertEqual(page.weekly_hours[0], [["09:00", "17:00"]])
        self.assertTrue(page.token)
        self.assertTrue(resp.data["share_url"].endswith(f"/calendar/book/{page.token}"))

    def test_the_calendar_of_another_user_is_refused(self):
        resp = self.create(calendar=str(self.others_calendar.uuid))

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("calendar", resp.data)
        self.assertFalse(BookingPage.objects.exists())

    def test_an_external_calendar_cannot_receive_appointments(self):
        feed = Calendar.objects.create(name="Feed", owner=self.owner)
        ExternalCalendar.objects.create(calendar=feed, url="https://example.com/a.ics")

        resp = self.create(calendar=str(feed.uuid))

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("calendar", resp.data)

    def test_an_external_calendar_can_be_checked_for_conflicts(self):
        feed = Calendar.objects.create(name="Feed", owner=self.owner)
        ExternalCalendar.objects.create(calendar=feed, url="https://example.com/a.ics")

        resp = self.create(busy_calendars=[str(feed.uuid)])

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["busy_calendars"], [feed.uuid])

    def test_busy_calendars_of_another_user_are_refused(self):
        resp = self.create(busy_calendars=[str(self.others_calendar.uuid)])

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("busy_calendars", resp.data)

    def test_the_mail_account_of_another_user_is_refused(self):
        theirs = mail_account(self.other, "other@example.com")

        resp = self.create(mail_account=str(theirs.uuid))

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("mail_account", resp.data)

    def test_own_mail_account_is_accepted(self):
        mine = mail_account(self.owner, "host@example.com")

        resp = self.create(mail_account=str(mine.uuid))

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(resp.data["mail_account"], mine.uuid)

    def test_weekly_hours_are_sorted(self):
        week = [[["14:00", "17:00"], ["09:00", "12:00"]]] + [[]] * 6

        resp = self.create(weekly_hours=week)

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        self.assertEqual(
            resp.data["weekly_hours"][0], [["09:00", "12:00"], ["14:00", "17:00"]]
        )

    def test_invalid_weekly_hours_are_refused(self):
        cases = {
            "six days": [[]] * 6,
            "bad time": [[["9:00", "12:00"]]] + [[]] * 6,
            "reversed": [[["12:00", "09:00"]]] + [[]] * 6,
            "overlap": [[["09:00", "12:00"], ["11:00", "13:00"]]] + [[]] * 6,
            "not a list": {"monday": []},
        }
        for label, week in cases.items():
            with self.subTest(label):
                resp = self.create(weekly_hours=week)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("weekly_hours", resp.data)

    def test_unknown_timezone_is_refused(self):
        resp = self.create(timezone="Mars/Olympus")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("timezone", resp.data)

    def test_duration_is_bounded(self):
        resp = self.create(duration_minutes=2)

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("duration_minutes", resp.data)

    def test_the_token_is_not_writable(self):
        resp = self.create(token="chosen")

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertNotEqual(resp.data["token"], "chosen")


class BookingPageAccessTests(BookingPageApiTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.page = BookingPage.objects.create(
            owner=self.owner, calendar=self.calendar, title="Mine", timezone="UTC"
        )
        self.theirs = BookingPage.objects.create(
            owner=self.other,
            calendar=self.others_calendar,
            title="Theirs",
            timezone="UTC",
        )

    def test_list_holds_only_my_pages(self):
        resp = self.client.get(LIST_URL)

        self.assertEqual([p["uuid"] for p in resp.data], [str(self.page.uuid)])

    def test_another_users_page_is_not_found(self):
        url = f"{LIST_URL}/{self.theirs.uuid}"
        for method in ("get", "patch", "delete"):
            with self.subTest(method):
                resp = getattr(self.client, method)(url, {"title": "x"}, format="json")
                self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(BookingPage.objects.filter(pk=self.theirs.pk).exists())

    def test_patch_updates_the_page(self):
        resp = self.client.patch(
            f"{LIST_URL}/{self.page.uuid}",
            {"title": "Office hours", "is_active": False},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.page.refresh_from_db()
        self.assertEqual(self.page.title, "Office hours")
        self.assertFalse(self.page.is_active)

    def test_patch_cannot_move_the_page_to_another_users_calendar(self):
        resp = self.client.patch(
            f"{LIST_URL}/{self.page.uuid}",
            {"calendar": str(self.others_calendar.uuid)},
            format="json",
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.page.refresh_from_db()
        self.assertEqual(self.page.calendar, self.calendar)

    def test_delete_keeps_the_booked_events(self):
        event = Event.objects.create(
            calendar=self.calendar,
            owner=self.owner,
            title="Booked",
            start=timezone.now() + timedelta(days=1),
            end=timezone.now() + timedelta(days=1, hours=1),
            source=Event.Source.BOOKING,
        )
        Booking.objects.create(
            page=self.page,
            start=event.start,
            end=event.end,
            guest_name="Ann",
            guest_email="ann@example.com",
            event=event,
        )

        resp = self.client.delete(f"{LIST_URL}/{self.page.uuid}")

        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(BookingPage.objects.filter(pk=self.page.pk).exists())
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())

    def test_unauthenticated_is_refused(self):
        self.client.force_authenticate(None)

        self.assertIn(
            self.client.get(LIST_URL).status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )


class BookingListTests(BookingPageApiTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.page = BookingPage.objects.create(
            owner=self.owner, calendar=self.calendar, title="Mine", timezone="UTC"
        )
        theirs = BookingPage.objects.create(
            owner=self.other,
            calendar=self.others_calendar,
            title="Theirs",
            timezone="UTC",
        )
        soon = timezone.now() + timedelta(days=1)
        self.mine = self.booking(self.page, soon)
        self.booking(theirs, soon)
        self.booking(self.page, timezone.now() - timedelta(days=1))

    def booking(self, page, start):
        return Booking.objects.create(
            page=page,
            start=start,
            end=start + timedelta(minutes=30),
            guest_name="Ann",
            guest_email="ann@example.com",
        )

    def test_lists_the_upcoming_bookings_of_my_pages_only(self):
        resp = self.client.get("/api/v1/bookings")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([b["uuid"] for b in resp.data], [str(self.mine.uuid)])
        self.assertEqual(resp.data[0]["page"]["title"], "Mine")

    def test_the_guest_token_is_never_serialized(self):
        resp = self.client.get("/api/v1/bookings")

        self.assertNotIn("token", resp.data[0])
        self.assertNotIn(self.mine.token, resp.content.decode())

    def test_page_filter_must_be_a_uuid(self):
        resp = self.client.get("/api/v1/bookings?page=nope")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_cancels_a_booking(self):
        event = Event.objects.create(
            calendar=self.calendar,
            owner=self.owner,
            title="Booked",
            start=self.mine.start,
            end=self.mine.end,
            source=Event.Source.BOOKING,
        )
        self.mine.event = event
        self.mine.save(update_fields=["event"])

        resp = self.client.post(f"/api/v1/bookings/{self.mine.uuid}/cancel")

        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.status, Booking.Status.CANCELLED)
        self.assertEqual(self.mine.cancelled_by, Booking.CancelledBy.OWNER)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

        again = self.client.post(f"/api/v1/bookings/{self.mine.uuid}/cancel")
        self.assertEqual(again.status_code, status.HTTP_409_CONFLICT)

    def test_cannot_cancel_a_booking_of_another_users_page(self):
        theirs = Booking.objects.exclude(page=self.page).get()

        resp = self.client.post(f"/api/v1/bookings/{theirs.uuid}/cancel")

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Booking.Status.CONFIRMED)
