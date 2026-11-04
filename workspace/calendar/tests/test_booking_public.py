from datetime import UTC, datetime, timedelta
from email import message_from_string
from email.header import decode_header, make_header
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from workspace.calendar.models import (
    Booking,
    BookingPage,
    Calendar,
    Event,
    EventMember,
)
from workspace.calendar.services.booking_mail import build_message
from workspace.mail.models import MailAccount
from workspace.notifications.models import Notification
from workspace.users.services.settings import set_setting

User = get_user_model()

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)  # a Friday
MONDAY_9_PARIS = datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


def frozen(moment=NOW):
    return patch("django.utils.timezone.now", return_value=moment)


class PublicBookingTestMixin:
    def setUp(self):
        self.owner = User.objects.create_user(
            username="host", password="pw", first_name="Hanna", last_name="Host"
        )
        self.calendar = Calendar.objects.create(name="Work", owner=self.owner)
        self.page = BookingPage.objects.create(
            owner=self.owner,
            calendar=self.calendar,
            title="Intro call",
            location="Room 4",
            duration_minutes=30,
            min_notice_hours=0,
            days_ahead=30,
            weekly_hours=[[["09:00", "11:00"]]] * 5 + [[], []],
            timezone="Europe/Paris",
        )
        self.base = f"/api/v1/booking-pages/shared/{self.page.token}"

    def tearDown(self):
        cache.clear()

    def slots(self, start="2026-11-09T00:00:00Z", end="2026-11-10T00:00:00Z"):
        with frozen():
            return self.client.get(f"{self.base}/slots", {"from": start, "to": end})

    def book(self, start=MONDAY_9_PARIS, **overrides):
        payload = {
            "start": start.isoformat(),
            "name": "Gina Guest",
            "email": "Gina@Example.com",
            "note": "About the quote",
            "timezone": "America/New_York",
        }
        payload.update(overrides)
        with frozen():
            return self.client.post(f"{self.base}/book", payload, format="json")


class SlotsEndpointTests(PublicBookingTestMixin, APITestCase):
    def test_returns_the_free_slots_as_instants(self):
        Event.objects.create(
            calendar=self.calendar,
            owner=self.owner,
            title="Dentist (private)",
            start=MONDAY_9_PARIS,
            end=MONDAY_9_PARIS + timedelta(hours=1),
        )

        resp = self.slots()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            resp.data["slots"],
            ["2026-11-09T09:00:00+00:00", "2026-11-09T09:30:00+00:00"],
        )
        self.assertNotIn("Dentist", resp.content.decode())

    def test_the_range_is_bounded(self):
        resp = self.slots(end="2027-01-09T00:00:00Z")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_naive_or_missing_bounds_are_refused(self):
        for start, end in (("2026-11-09T00:00:00", "2026-11-10T00:00:00Z"), ("", "")):
            with self.subTest(start=start):
                resp = self.slots(start=start, end=end)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_inactive_or_unknown_page_is_not_found(self):
        self.page.is_active = False
        self.page.save(update_fields=["is_active"])

        self.assertEqual(self.slots().status_code, status.HTTP_404_NOT_FOUND)
        with frozen():
            resp = self.client.get(
                "/api/v1/booking-pages/shared/nope/slots",
                {"from": "2026-11-09T00:00:00Z", "to": "2026-11-10T00:00:00Z"},
            )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class BookEndpointTests(PublicBookingTestMixin, APITestCase):
    def test_booking_puts_the_appointment_on_the_owners_calendar(self):
        resp = self.book()

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        booking = Booking.objects.get()
        self.assertEqual(booking.start, MONDAY_9_PARIS)
        self.assertEqual(booking.end, MONDAY_9_PARIS + timedelta(minutes=30))
        self.assertEqual(booking.timezone, "America/New_York")

        event = booking.event
        self.assertEqual(event.calendar, self.calendar)
        self.assertEqual(event.owner, self.owner)
        self.assertEqual(event.source, Event.Source.BOOKING)
        self.assertEqual(event.title, "Intro call — Gina Guest")
        self.assertEqual(event.location, "Room 4")
        self.assertEqual(event.ical_uid, booking.ical_uid)
        guest = EventMember.objects.get(event=event)
        self.assertEqual(guest.email, "gina@example.com")
        self.assertEqual(guest.status, EventMember.Status.ACCEPTED)

        self.assertTrue(
            resp.data["manage_url"].endswith(f"/calendar/bookings/{booking.token}")
        )
        self.assertFalse(resp.data["mail_sent"])
        self.assertEqual(resp.data["owner_name"], "Hanna Host")

    def test_the_owner_is_notified(self):
        self.book()

        notification = Notification.objects.get(recipient=self.owner)
        self.assertIn("Intro call", notification.title)
        self.assertIn("Gina Guest", notification.body)
        self.assertEqual(notification.event, Booking.objects.get().event)

    def test_a_taken_slot_answers_409(self):
        """The second guest on the same slot loses cleanly: the slot is
        checked again under the lock, after the first booking committed."""
        self.assertEqual(self.book().status_code, status.HTTP_201_CREATED)

        resp = self.book(email="other@example.com", name="Other")

        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Booking.objects.count(), 1)

    def test_the_constraint_backs_the_lock(self):
        """Should two bookings slip past the check, the database still keeps
        one confirmed booking per slot and page."""
        with patch(
            "workspace.calendar.services.bookings.is_bookable", return_value=True
        ):
            self.assertEqual(self.book().status_code, status.HTTP_201_CREATED)
            resp = self.book(email="other@example.com")

        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Booking.objects.count(), 1)
        self.assertEqual(Event.objects.count(), 1)

    def test_a_busy_or_misaligned_start_is_refused(self):
        Event.objects.create(
            calendar=self.calendar,
            owner=self.owner,
            title="Busy",
            start=MONDAY_9_PARIS,
            end=MONDAY_9_PARIS + timedelta(minutes=30),
        )
        for start in (MONDAY_9_PARIS, MONDAY_9_PARIS + timedelta(minutes=40)):
            with self.subTest(start=start):
                self.assertEqual(
                    self.book(start=start).status_code, status.HTTP_409_CONFLICT
                )
        self.assertFalse(Booking.objects.exists())

    def test_an_inactive_page_takes_no_booking(self):
        self.page.is_active = False
        self.page.save(update_fields=["is_active"])

        self.assertEqual(self.book().status_code, status.HTTP_404_NOT_FOUND)

    def test_name_and_email_are_required(self):
        resp = self.book(name="", email="not-an-email")

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("name", resp.data)
        self.assertIn("email", resp.data)

    def test_booking_is_rate_limited_per_ip(self):
        with patch.dict(
            SimpleRateThrottle.THROTTLE_RATES, {"calendar.booking.write.ip": "1/hour"}
        ):
            self.book()
            resp = self.book(start=MONDAY_9_PARIS + timedelta(minutes=30))

        self.assertEqual(resp.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_the_confirmation_mail_is_queued_after_commit(self):
        account = MailAccount.objects.create(
            owner=self.owner,
            email="hanna@example.com",
            imap_host="imap.test",
            smtp_host="smtp.test",
            username="hanna",
        )
        self.page.mail_account = account
        self.page.save(update_fields=["mail_account"])

        with (
            patch("workspace.calendar.tasks.send_booking_mail.delay") as delay,
            self.captureOnCommitCallbacks(execute=True),
        ):
            resp = self.book()

        self.assertTrue(resp.data["mail_sent"])
        booking = Booking.objects.get()
        delay.assert_called_once_with(
            str(booking.pk), "confirmed", "http://testserver/"
        )


class GuestCancelTests(PublicBookingTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.book().status_code, status.HTTP_201_CREATED)
        self.booking = Booking.objects.get()
        Notification.objects.all().delete()

    def cancel(self, token=None, moment=NOW):
        with frozen(moment):
            return self.client.post(
                f"/api/v1/bookings/shared/{token or self.booking.token}/cancel"
            )

    def test_guest_cancels_from_the_link(self):
        event_id = self.booking.event_id

        resp = self.cancel()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["status"], Booking.Status.CANCELLED)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.cancelled_by, Booking.CancelledBy.GUEST)
        self.assertFalse(Event.objects.filter(pk=event_id).exists())
        notification = Notification.objects.get(recipient=self.owner)
        self.assertIn("Gina Guest", notification.body)

    def test_the_slot_is_free_again(self):
        self.cancel()

        self.assertEqual(self.book().status_code, status.HTTP_201_CREATED)

    def test_an_unknown_token_is_not_found(self):
        self.assertEqual(self.cancel("nope").status_code, status.HTTP_404_NOT_FOUND)

    def test_a_started_appointment_cannot_be_cancelled(self):
        resp = self.cancel(moment=MONDAY_9_PARIS + timedelta(minutes=5))

        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CONFIRMED)

    def test_cancelling_twice_is_harmless(self):
        self.cancel()
        resp = self.cancel()

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(recipient=self.owner).count(), 1)


class CalendarSideTests(PublicBookingTestMixin, APITestCase):
    """The owner manages the appointment as an event; the booking follows."""

    def setUp(self):
        super().setUp()
        self.assertEqual(self.book().status_code, status.HTTP_201_CREATED)
        self.booking = Booking.objects.get()
        self.client.force_authenticate(self.owner)

    def test_deleting_the_event_cancels_the_booking(self):
        resp = self.client.delete(f"/api/v1/events/{self.booking.event_id}")

        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status, Booking.Status.CANCELLED)
        self.assertEqual(self.booking.cancelled_by, Booking.CancelledBy.OWNER)

    def test_moving_the_event_moves_the_booking(self):
        event = self.booking.event
        event.start += timedelta(hours=2)
        event.end += timedelta(hours=2)
        event.save()

        self.booking.refresh_from_db()
        self.assertEqual(self.booking.start, MONDAY_9_PARIS + timedelta(hours=2))

    def test_stacking_two_appointments_does_not_fail_the_move(self):
        self.client.force_authenticate(None)
        self.assertEqual(
            self.book(start=MONDAY_9_PARIS + timedelta(minutes=30)).status_code,
            status.HTTP_201_CREATED,
        )
        second = Booking.objects.exclude(pk=self.booking.pk).get()

        event = second.event
        event.start = MONDAY_9_PARIS
        event.end = MONDAY_9_PARIS + timedelta(minutes=30)
        event.save()

        second.refresh_from_db()
        self.assertEqual(second.start, MONDAY_9_PARIS + timedelta(minutes=30))


class BookingMailTests(PublicBookingTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.account = MailAccount.objects.create(
            owner=self.owner,
            email="hanna@example.com",
            imap_host="imap.test",
            smtp_host="smtp.test",
            username="hanna",
        )
        self.page.mail_account = self.account
        self.page.save(update_fields=["mail_account"])
        with patch("workspace.calendar.tasks.send_booking_mail.delay"):
            self.book()
        self.booking = Booking.objects.select_related("page__owner").get()

    def parts(self, message):
        return {part.get_content_type(): part for part in message.walk()}

    def test_confirmation_carries_a_request_and_the_cancel_link(self):
        message = message_from_string(
            build_message(self.booking, "confirmed", "https://ws.example").as_string()
        )

        self.assertIn("hanna@example.com", message["From"])
        self.assertIn("Gina@Example.com", message["To"])
        self.assertTrue(message["Subject"].startswith("Confirmed: Intro call"))
        parts = self.parts(message)
        body = parts["text/plain"].get_payload(decode=True).decode()
        self.assertIn(
            f"https://ws.example/calendar/bookings/{self.booking.token}", body
        )
        # The guest browsed from New York: 9:00 in Paris is 3:00 there.
        self.assertIn("03:00 (America/New_York)", body)
        ics = parts["text/calendar"].get_payload(decode=True).decode()
        self.assertIn("METHOD:REQUEST", ics)
        self.assertIn(f"UID:{self.booking.ical_uid}", ics)
        self.assertIn('ORGANIZER;CN="Hanna Host":mailto:hanna@example.com', ics)
        self.assertIn("TZID=Europe/Paris", ics)

    def test_cancellation_carries_a_cancel_under_the_same_uid(self):
        message = message_from_string(
            build_message(self.booking, "cancelled").as_string()
        )

        ics = self.parts(message)["text/calendar"].get_payload(decode=True).decode()
        self.assertIn("METHOD:CANCEL", ics)
        self.assertIn("SEQUENCE:1", ics)
        self.assertIn(f"UID:{self.booking.ical_uid}", ics)
        self.assertNotIn("/calendar/bookings/", message.as_string())

    def test_the_mail_is_in_the_owners_language(self):
        set_setting(self.owner, "core", "language", "fr")

        message = build_message(self.booking, "confirmed", "https://ws.example")

        subject = str(make_header(decode_header(message["Subject"])))
        self.assertTrue(subject.startswith("Confirmé"), subject)
        body = self.parts(message)["text/plain"].get_payload(decode=True).decode()
        self.assertIn("Bonjour Gina Guest", body)

    def test_the_task_sends_through_the_page_account(self):
        from workspace.calendar.tasks import send_booking_mail

        with patch("workspace.mail.services.smtp.connect_smtp") as connect:
            send_booking_mail(str(self.booking.pk), "confirmed", "https://ws.example")

        connect.assert_called_once_with(self.account)
        server = connect.return_value
        sender, recipients, _raw = server.sendmail.call_args.args
        self.assertEqual(sender, "hanna@example.com")
        self.assertEqual(recipients, ["Gina@Example.com"])

    def test_nothing_is_sent_for_a_deactivated_owner(self):
        from workspace.calendar.tasks import send_booking_mail

        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])

        with patch("workspace.mail.services.smtp.connect_smtp") as connect:
            send_booking_mail(str(self.booking.pk), "confirmed", "")

        connect.assert_not_called()


class PublicPagesTests(PublicBookingTestMixin, APITestCase):
    def test_the_booking_page_renders_for_anyone(self):
        resp = self.client.get(f"/calendar/book/{self.page.token}")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertContains(resp, "Intro call")
        self.assertContains(resp, "Hanna Host")
        # What the visitor learns is the page, not the calendar behind it.
        self.assertNotContains(resp, str(self.calendar.uuid))

    def test_an_inactive_page_is_not_found(self):
        self.page.is_active = False
        self.page.save(update_fields=["is_active"])

        resp = self.client.get(f"/calendar/book/{self.page.token}")

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_the_page_speaks_its_owners_language(self):
        set_setting(self.owner, "core", "language", "fr")

        resp = self.client.get(f"/calendar/book/{self.page.token}")

        self.assertContains(resp, 'lang="fr"')
        self.assertContains(resp, "Fuseau horaire")

    def test_the_guest_manages_the_booking_from_its_link(self):
        self.assertEqual(self.book().status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get()

        with frozen():
            resp = self.client.get(f"/calendar/bookings/{booking.token}")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertContains(resp, "Gina Guest")
        self.assertNotContains(resp, "Gina@Example.com")
        self.assertEqual(self.client.get("/calendar/bookings/nope").status_code, 404)

    def test_the_guest_downloads_the_appointment(self):
        self.assertEqual(self.book().status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get()

        resp = self.client.get(f"/calendar/bookings/{booking.token}/event.ics")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "text/calendar; charset=utf-8")
        ics = resp.content.decode()
        self.assertIn("METHOD:PUBLISH", ics)
        self.assertIn(f"UID:{booking.ical_uid}", ics)
        self.assertNotIn("ATTENDEE", ics)

    def test_a_cancelled_booking_has_nothing_to_download(self):
        self.assertEqual(self.book().status_code, status.HTTP_201_CREATED)
        booking = Booking.objects.get()
        booking.status = Booking.Status.CANCELLED
        booking.save(update_fields=["status"])

        resp = self.client.get(f"/calendar/bookings/{booking.token}/event.ics")

        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class DeactivatedOwnerTests(PublicBookingTestMixin, APITestCase):
    """A deactivated account takes no more appointments, whoever holds its link."""

    def setUp(self):
        super().setUp()
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])

    def test_the_page_is_not_found(self):
        self.assertEqual(
            self.client.get(f"/calendar/book/{self.page.token}").status_code, 404
        )

    def test_no_slot_is_offered(self):
        self.assertEqual(self.slots().status_code, status.HTTP_404_NOT_FOUND)

    def test_nothing_can_be_booked(self):
        self.assertEqual(self.book().status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Booking.objects.exists())


class StaleConfirmationTests(PublicBookingTestMixin, APITestCase):
    def test_a_confirmation_queued_before_a_cancellation_is_not_sent(self):
        from workspace.calendar.tasks import send_booking_mail

        self.page.mail_account = MailAccount.objects.create(
            owner=self.owner,
            email="hanna@example.com",
            imap_host="imap.test",
            smtp_host="smtp.test",
            username="hanna",
        )
        self.page.save(update_fields=["mail_account"])
        with patch("workspace.calendar.tasks.send_booking_mail.delay"):
            self.book()
        booking = Booking.objects.get()
        booking.status = Booking.Status.CANCELLED
        booking.save(update_fields=["status"])

        with patch("workspace.mail.services.smtp.connect_smtp") as connect:
            send_booking_mail(str(booking.pk), "confirmed", "https://ws.example")

        connect.assert_not_called()
