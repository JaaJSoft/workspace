from datetime import UTC, datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.calendar.models import Calendar, Event, EventMember
from workspace.calendar.services.event_scope import update_event
from workspace.calendar.services.guests import sync_guests
from workspace.calendar.services.recurrence_rule import apply_rule

from .test_calendar import CalendarTestMixin

User = get_user_model()


class EventMemberConstraintTests(CalendarTestMixin, TestCase):
    def test_guest_row_needs_an_email(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventMember.objects.create(event=self.event)

    def test_account_row_cannot_carry_an_email(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventMember.objects.create(
                event=self.event, user=self.outsider, email="x@example.com"
            )

    def test_one_row_per_guest_email(self):
        EventMember.objects.create(event=self.event, email="ada@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventMember.objects.create(event=self.event, email="ada@example.com")


class SyncGuestsTests(CalendarTestMixin, TestCase):
    def _guest_emails(self, event):
        return sorted(
            event.members.filter(user__isnull=True).values_list("email", flat=True)
        )

    def test_creates_normalized_and_deduped_rows(self):
        sync_guests(
            self.event,
            [
                {"email": " Ada@Example.com ", "name": "Ada"},
                {"email": "ada@example.com", "name": "Duplicate"},
                {"email": "bob@example.com"},
            ],
        )
        guests = {m.email: m.name for m in self.event.members.filter(user__isnull=True)}
        self.assertEqual(guests, {"ada@example.com": "Ada", "bob@example.com": ""})

    def test_replaces_the_guest_list_and_leaves_accounts_alone(self):
        sync_guests(self.event, [{"email": "ada@example.com", "name": "Ada"}])
        sync_guests(self.event, [{"email": "bob@example.com", "name": "Bob"}])
        self.assertEqual(self._guest_emails(self.event), ["bob@example.com"])
        self.assertTrue(self.event.members.filter(user=self.member).exists())

    def test_keeps_an_existing_guest_row_and_its_status(self):
        sync_guests(self.event, [{"email": "ada@example.com", "name": "Ada"}])
        row = self.event.members.get(email="ada@example.com")
        EventMember.objects.filter(pk=row.pk).update(status=EventMember.Status.ACCEPTED)

        sync_guests(self.event, [{"email": "ada@example.com", "name": "Ada L."}])

        row.refresh_from_db()
        self.assertEqual(row.status, EventMember.Status.ACCEPTED)
        self.assertEqual(row.name, "Ada L.")


class GuestsApiTests(CalendarTestMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(self.owner)

    def _create(self, **overrides):
        data = {
            "calendar_id": str(self.calendar.uuid),
            "title": "Dinner",
            "start": (timezone.now() + timedelta(days=2)).isoformat(),
            "end": (timezone.now() + timedelta(days=2, hours=1)).isoformat(),
        }
        data.update(overrides)
        return self.client.post("/api/v1/events", data, format="json")

    def test_create_with_guests(self):
        resp = self._create(
            member_ids=[self.member.id],
            guests=[{"email": "Ada@Example.com", "name": "Ada Lovelace"}],
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        guests = [m for m in resp.data["members"] if m["user"] is None]
        self.assertEqual(len(guests), 1)
        self.assertEqual(guests[0]["email"], "ada@example.com")
        self.assertEqual(guests[0]["name"], "Ada Lovelace")
        self.assertEqual(guests[0]["status"], "pending")
        accounts = [m["user"]["id"] for m in resp.data["members"] if m["user"]]
        self.assertEqual(accounts, [self.member.id])

    def test_create_rejects_a_malformed_guest_email(self):
        resp = self._create(guests=[{"email": "not-an-email"}])
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_replaces_guests_only_when_sent(self):
        sync_guests(self.event, [{"email": "ada@example.com", "name": "Ada"}])
        url = f"/api/v1/events/{self.event.uuid}"

        resp = self.client.put(url, {"member_ids": []}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [m["email"] for m in resp.data["members"]], ["ada@example.com"]
        )

        resp = self.client.put(url, {"guests": []}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["members"], [])

    def test_guest_does_not_break_the_event_listing(self):
        sync_guests(self.event, [{"email": "ada@example.com", "name": "Ada"}])
        resp = self.client.get(
            "/api/v1/events",
            {
                "start": timezone.now().isoformat(),
                "end": (timezone.now() + timedelta(days=7)).isoformat(),
            },
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        event = next(e for e in resp.data if e["uuid"] == str(self.event.uuid))
        self.assertIn("ada@example.com", [m["email"] for m in event["members"]])

    def test_event_card_renders_a_guest(self):
        sync_guests(self.event, [{"email": "ada@example.com", "name": "Ada"}])
        self.client.force_login(self.owner)
        resp = self.client.get(f"/calendar/events/{self.event.uuid}/card")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertContains(resp, 'title="Ada &lt;ada@example.com&gt;"')


class RecurringGuestsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", password="x")
        self.member = User.objects.create_user(username="member", password="x")
        self.calendar = Calendar.objects.create(name="Work", owner=self.owner)
        self.master = Event(
            calendar=self.calendar,
            title="Standup",
            start=datetime(2030, 1, 7, 9, tzinfo=UTC),
            end=datetime(2030, 1, 7, 9, 30, tzinfo=UTC),
            owner=self.owner,
        )
        apply_rule(self.master, "RRULE:FREQ=DAILY;COUNT=10")
        self.master.save()
        EventMember.objects.create(event=self.master, user=self.member)
        sync_guests(self.master, [{"email": "ada@example.com", "name": "Ada"}])

    def _members(self, event):
        return sorted(str(m.user_id or m.email) for m in event.members.all())

    def test_this_occurrence_inherits_guests(self):
        exc = update_event(
            self.master,
            {"title": "Moved"},
            self.owner,
            scope="this",
            original_start=datetime(2030, 1, 9, 9, tzinfo=UTC),
        )
        self.assertEqual(self._members(exc), self._members(self.master))

    def test_future_split_inherits_guests_when_only_accounts_change(self):
        new_master = update_event(
            self.master,
            {"member_ids": []},
            self.owner,
            scope="future",
            original_start=datetime(2030, 1, 9, 9, tzinfo=UTC),
        )
        self.assertEqual(self._members(new_master), ["ada@example.com"])

    def test_future_split_takes_the_guests_it_is_given(self):
        new_master = update_event(
            self.master,
            {"guests": [{"email": "bob@example.com", "name": "Bob"}]},
            self.owner,
            scope="future",
            original_start=datetime(2030, 1, 9, 9, tzinfo=UTC),
        )
        self.assertEqual(
            self._members(new_master), sorted([str(self.member.id), "bob@example.com"])
        )
