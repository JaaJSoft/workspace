from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.calendar.models import (
    Booking,
    BookingPage,
    Calendar,
    Event,
    EventMember,
)
from workspace.calendar.services.booking_slots import free_slots, is_bookable
from workspace.calendar.services.recurrence_rule import apply_rule

User = get_user_model()

PARIS_MONDAY = datetime(2026, 11, 9, tzinfo=UTC)  # Monday; Paris is UTC+1
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)  # the Friday before


def hours(*ranges):
    """Weekly hours with the same *ranges* on every weekday, none at weekends."""
    return [[list(r) for r in ranges] for _ in range(5)] + [[], []]


def utc(*args):
    return datetime(*args, tzinfo=UTC)


class SlotTestMixin:
    def setUp(self):
        self.owner = User.objects.create_user(username="host", password="pw")
        self.calendar = Calendar.objects.create(name="Work", owner=self.owner)
        self.page = BookingPage.objects.create(
            owner=self.owner,
            calendar=self.calendar,
            title="Intro call",
            duration_minutes=30,
            min_notice_hours=0,
            days_ahead=60,
            weekly_hours=hours(("09:00", "11:00")),
            timezone="Europe/Paris",
        )

    def monday_slots(self, page=None, now=NOW):
        return free_slots(
            page or self.page,
            PARIS_MONDAY,
            PARIS_MONDAY + timedelta(days=1),
            now=now,
        )

    def event(self, start, end, calendar=None, **kwargs):
        return Event.objects.create(
            calendar=calendar or self.calendar,
            owner=kwargs.pop("owner", self.owner),
            title=kwargs.pop("title", "Busy"),
            start=start,
            end=end,
            **kwargs,
        )


class CandidateSlotTests(SlotTestMixin, TestCase):
    def test_weekly_hours_are_read_on_the_page_wall_clock(self):
        # 9:00-11:00 in Paris is 8:00-10:00 UTC in November.
        self.assertEqual(
            self.monday_slots(),
            [
                utc(2026, 11, 9, 8),
                utc(2026, 11, 9, 8, 30),
                utc(2026, 11, 9, 9),
                utc(2026, 11, 9, 9, 30),
            ],
        )

    def test_a_slot_must_end_within_its_range(self):
        self.page.duration_minutes = 45
        self.page.save(update_fields=["duration_minutes"])

        # 9:00 and 9:45 fit; 10:30 would end at 11:15.
        self.assertEqual(
            self.monday_slots(), [utc(2026, 11, 9, 8), utc(2026, 11, 9, 8, 45)]
        )

    def test_hours_keep_their_wall_clock_across_a_dst_change(self):
        """Paris springs forward on Sunday 28 March 2027: 9:00 is 8:00 UTC on
        the Friday before and 7:00 UTC on the Monday after."""
        self.page.weekly_hours = hours(("09:00", "09:30"))
        self.page.save(update_fields=["weekly_hours"])
        now = utc(2027, 3, 20)

        slots = free_slots(self.page, utc(2027, 3, 26), utc(2027, 3, 30), now=now)

        self.assertEqual(slots, [utc(2027, 3, 26, 8), utc(2027, 3, 29, 7)])

    def test_weekends_without_hours_have_no_slot(self):
        saturday = PARIS_MONDAY - timedelta(days=2)
        self.assertEqual(free_slots(self.page, saturday, PARIS_MONDAY, now=NOW), [])

    def test_minimum_notice_drops_the_slots_too_close(self):
        self.page.min_notice_hours = 1
        self.page.save(update_fields=["min_notice_hours"])
        now = utc(2026, 11, 9, 7, 45)

        self.assertEqual(self.monday_slots(now=now)[0], utc(2026, 11, 9, 9))

    def test_nothing_is_bookable_past_the_horizon(self):
        self.page.days_ahead = 2
        self.page.save(update_fields=["days_ahead"])

        self.assertEqual(self.monday_slots(), [])


class BusyTimeTests(SlotTestMixin, TestCase):
    def test_an_event_takes_the_slots_it_overlaps(self):
        self.event(utc(2026, 11, 9, 8, 15), utc(2026, 11, 9, 8, 45))

        self.assertEqual(
            self.monday_slots(), [utc(2026, 11, 9, 9), utc(2026, 11, 9, 9, 30)]
        )

    def test_the_buffer_keeps_free_time_around_other_events(self):
        self.page.buffer_minutes = 15
        self.page.save(update_fields=["buffer_minutes"])
        self.event(utc(2026, 11, 9, 9, 30), utc(2026, 11, 9, 10))

        # 9:00 ends where the event starts, inside its 15 minutes of buffer.
        self.assertEqual(
            self.monday_slots(), [utc(2026, 11, 9, 8), utc(2026, 11, 9, 8, 30)]
        )

    def test_an_event_touching_a_slot_does_not_take_it(self):
        self.event(utc(2026, 11, 9, 7, 30), utc(2026, 11, 9, 8))

        self.assertEqual(self.monday_slots()[0], utc(2026, 11, 9, 8))

    def test_an_all_day_event_takes_the_whole_local_day(self):
        """An all-day row is the UTC midnight of its day label; the day it
        blocks is the page's, here a Tokyo page whose Monday starts at
        Sunday 15:00 UTC."""
        self.page.timezone = "Asia/Tokyo"
        self.page.save(update_fields=["timezone"])
        self.event(utc(2026, 11, 9), utc(2026, 11, 10), all_day=True)

        tokyo_monday = utc(2026, 11, 8, 15)
        tokyo_tuesday = tokyo_monday + timedelta(days=1)
        self.assertEqual(
            free_slots(self.page, tokyo_monday, tokyo_tuesday, now=NOW), []
        )
        self.assertEqual(
            len(
                free_slots(
                    self.page, tokyo_tuesday, tokyo_tuesday + timedelta(days=1), now=NOW
                )
            ),
            4,
        )

    def test_recurring_occurrences_are_busy_except_the_cancelled_one(self):
        master = self.event(utc(2026, 11, 2, 8), utc(2026, 11, 2, 9))
        apply_rule(master, "RRULE:FREQ=WEEKLY")
        master.save()
        next_monday = PARIS_MONDAY + timedelta(days=7)
        self.event(
            utc(2026, 11, 16, 8),
            utc(2026, 11, 16, 9),
            recurrence_parent=master,
            original_start=utc(2026, 11, 16, 8),
            is_cancelled=True,
        )

        self.assertEqual(
            self.monday_slots(), [utc(2026, 11, 9, 9), utc(2026, 11, 9, 9, 30)]
        )
        self.assertEqual(
            len(
                free_slots(
                    self.page, next_monday, next_monday + timedelta(days=1), now=NOW
                )
            ),
            4,
        )

    def test_a_moved_occurrence_is_busy_at_its_new_time(self):
        master = self.event(utc(2026, 11, 2, 8), utc(2026, 11, 2, 9))
        apply_rule(master, "RRULE:FREQ=WEEKLY")
        master.save()
        self.event(
            utc(2026, 11, 9, 9, 30),
            utc(2026, 11, 9, 10),
            recurrence_parent=master,
            original_start=utc(2026, 11, 9, 8),
        )

        self.assertEqual(
            self.monday_slots(),
            [utc(2026, 11, 9, 8), utc(2026, 11, 9, 8, 30), utc(2026, 11, 9, 9)],
        )

    def test_only_the_checked_calendars_count(self):
        family = Calendar.objects.create(name="Family", owner=self.owner)
        self.event(utc(2026, 11, 9, 8), utc(2026, 11, 9, 10), calendar=family)

        self.assertEqual(len(self.monday_slots()), 4)

        self.page.busy_calendars.add(family)
        self.assertEqual(self.monday_slots(), [])

    def test_an_invitation_counts_unless_declined(self):
        colleague = User.objects.create_user(username="colleague", password="pw")
        theirs = Calendar.objects.create(name="Theirs", owner=colleague)
        meeting = self.event(
            utc(2026, 11, 9, 8), utc(2026, 11, 9, 10), calendar=theirs, owner=colleague
        )
        membership = EventMember.objects.create(event=meeting, user=self.owner)

        self.assertEqual(self.monday_slots(), [])

        membership.status = EventMember.Status.DECLINED
        membership.save(update_fields=["status"])
        self.assertEqual(len(self.monday_slots()), 4)

    def test_another_users_events_never_count(self):
        stranger = User.objects.create_user(username="stranger", password="pw")
        theirs = Calendar.objects.create(name="Theirs", owner=stranger)
        self.event(
            utc(2026, 11, 9, 8), utc(2026, 11, 9, 10), calendar=theirs, owner=stranger
        )

        self.assertEqual(len(self.monday_slots()), 4)

    def test_an_appointment_of_another_page_takes_the_owners_time(self):
        """Two pages share one person: an appointment booked through the
        other page blocks this one even on a calendar it does not check."""
        elsewhere = Calendar.objects.create(name="Clients", owner=self.owner)
        other = BookingPage.objects.create(
            owner=self.owner,
            calendar=elsewhere,
            title="Consulting",
            weekly_hours=hours(("09:00", "11:00")),
            timezone="Europe/Paris",
        )
        event = self.event(
            utc(2026, 11, 9, 8),
            utc(2026, 11, 9, 9),
            calendar=elsewhere,
            source=Event.Source.BOOKING,
        )
        Booking.objects.create(
            page=other,
            start=event.start,
            end=event.end,
            guest_name="Ann",
            guest_email="ann@example.com",
            event=event,
        )

        self.assertEqual(
            self.monday_slots(), [utc(2026, 11, 9, 9), utc(2026, 11, 9, 9, 30)]
        )

    def test_is_bookable_only_for_a_free_slot_start(self):
        self.event(utc(2026, 11, 9, 8), utc(2026, 11, 9, 8, 30))

        self.assertFalse(is_bookable(self.page, utc(2026, 11, 9, 8), now=NOW))
        self.assertTrue(is_bookable(self.page, utc(2026, 11, 9, 8, 30), now=NOW))
        self.assertFalse(is_bookable(self.page, utc(2026, 11, 9, 8, 40), now=NOW))
