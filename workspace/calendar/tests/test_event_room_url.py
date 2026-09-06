"""``room_url`` - the member door to an event's meeting.

``join_url`` is the public guest link and is offered to anyone who can read
the event. ``room_url`` is the member room, so it is offered only to someone
the room view would actually let in: an active member of the meeting's
conversation. A viewer of a shared calendar gets ``null``.
"""

from django.contrib.auth import get_user_model
from django.db import connection
from django.template.loader import render_to_string
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.calendar.models import (
    Calendar,
    CalendarSubscription,
    Event,
    EventMember,
)
from workspace.calendar.services.recurrence_rule import apply_rule

User = get_user_model()


def _room_url(meeting):
    return f"http://testserver/chat/room/{meeting.conversation_id}"


class EventRoomUrlTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", "owner@example.com", "pw")
        self.member = User.objects.create_user("member", "member@example.com", "pw")
        self.viewer = User.objects.create_user("viewer", "viewer@example.com", "pw")
        self.calendar = Calendar.objects.create(owner=self.owner, name="Cal")
        # rrule walks to the second, so a series whose start carries
        # microseconds produces occurrence keys an exception's
        # original_start can never match.
        self.now = timezone.now().replace(microsecond=0)
        # Both non-owners read every event on the calendar; only the invited
        # one lands in the meeting's conversation, which is what separates a
        # member from a mere viewer.
        for user in (self.member, self.viewer):
            CalendarSubscription.objects.create(user=user, calendar=self.calendar)
        self.event = Event.objects.create(
            calendar=self.calendar,
            owner=self.owner,
            title="Sync",
            start=self.now + timezone.timedelta(hours=1),
            end=self.now + timezone.timedelta(hours=2),
        )
        EventMember.objects.create(event=self.event, user=self.member)
        self.client.force_login(self.member)

    def _create_meeting(self, event):
        from workspace.chat.services.meetings import create_meeting

        return create_meeting(event, self.owner)

    def _range_url(self):
        start = (timezone.now() - timezone.timedelta(days=1)).isoformat()
        end = (timezone.now() + timezone.timedelta(days=3)).isoformat()
        return f"/api/v1/events?start={start}&end={end}"

    def _upcoming_url(self):
        return f"/api/v1/events?after={timezone.now().isoformat()}&limit=20"

    def _recurring_series_with_exception(self, title):
        master = Event(
            calendar=self.calendar,
            owner=self.owner,
            title=title,
            start=self.now + timezone.timedelta(hours=1),
            end=self.now + timezone.timedelta(hours=2),
        )
        apply_rule(master, "RRULE:FREQ=DAILY")
        master.save()
        EventMember.objects.create(event=master, user=self.member)
        meeting = self._create_meeting(master)
        occ_start = master.start + timezone.timedelta(days=1)
        exception = Event.objects.create(
            calendar=self.calendar,
            owner=self.owner,
            title=f"{title} (moved)",
            start=occ_start + timezone.timedelta(minutes=30),
            end=occ_start + timezone.timedelta(minutes=90),
            recurrence_parent=master,
            original_start=occ_start,
        )
        return master, exception, meeting

    # ---- detail ----

    def test_room_url_is_null_without_a_meeting(self):
        resp = self.client.get(f"/api/v1/events/{self.event.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["room_url"])

    def test_member_gets_the_absolute_room_url(self):
        meeting = self._create_meeting(self.event)
        resp = self.client.get(f"/api/v1/events/{self.event.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["room_url"], _room_url(meeting))

    def test_viewer_who_is_not_a_conversation_member_gets_null(self):
        meeting = self._create_meeting(self.event)
        self.client.force_login(self.viewer)
        resp = self.client.get(f"/api/v1/events/{self.event.uuid}")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        # The guest link stays public; only the member room is withheld.
        self.assertEqual(payload["join_url"], f"http://testserver/meet/{meeting.slug}")
        self.assertIsNone(payload["room_url"])

    def test_owner_who_left_the_conversation_gets_null(self):
        from workspace.chat.models import ConversationMember

        meeting = self._create_meeting(self.event)
        ConversationMember.objects.filter(
            conversation_id=meeting.conversation_id, user=self.owner
        ).update(left_at=timezone.now())
        self.client.force_login(self.owner)
        resp = self.client.get(f"/api/v1/events/{self.event.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["room_url"])

    def test_exception_detail_reads_the_series_room_url(self):
        # The detail GET is fetched by whatever uuid the grid handed it,
        # which for a materialized exception is the exception's own row -
        # but the Meeting lives on the series master.
        _master, exception, meeting = self._recurring_series_with_exception("Standup")
        resp = self.client.get(f"/api/v1/events/{exception.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["room_url"], _room_url(meeting))

    # ---- range listing ----

    def test_range_listing_carries_the_room_url(self):
        meeting = self._create_meeting(self.event)
        resp = self.client.get(self._range_url())
        matching = [e for e in resp.json() if e["uuid"] == str(self.event.uuid)]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["room_url"], _room_url(meeting))

    def test_range_listing_is_null_for_a_non_member(self):
        self._create_meeting(self.event)
        self.client.force_login(self.viewer)
        resp = self.client.get(self._range_url())
        matching = [e for e in resp.json() if e["uuid"] == str(self.event.uuid)]
        self.assertEqual(len(matching), 1)
        self.assertIsNone(matching[0]["room_url"])

    def test_recurring_occurrences_carry_the_room_url(self):
        _master, _exception, meeting = self._recurring_series_with_exception("Standup")
        resp = self.client.get(self._range_url())
        occurrences = [e for e in resp.json() if e["title"].startswith("Standup")]
        self.assertGreaterEqual(len(occurrences), 3)
        for occ in occurrences:
            self.assertEqual(occ["room_url"], _room_url(meeting), occ["uuid"])

    def test_materialized_exception_in_the_listing_carries_the_room_url(self):
        _master, exception, meeting = self._recurring_series_with_exception("Standup")
        resp = self.client.get(self._range_url())
        matching = [e for e in resp.json() if e["uuid"] == str(exception.uuid)]
        self.assertEqual(len(matching), 1)
        self.assertTrue(matching[0]["is_exception"])
        self.assertEqual(matching[0]["room_url"], _room_url(meeting))

    def test_recurring_occurrences_are_null_for_a_non_member(self):
        self._recurring_series_with_exception("Standup")
        self.client.force_login(self.viewer)
        resp = self.client.get(self._range_url())
        occurrences = [e for e in resp.json() if e["title"].startswith("Standup")]
        self.assertGreaterEqual(len(occurrences), 3)
        for occ in occurrences:
            self.assertIsNone(occ["room_url"], occ["uuid"])

    # ---- upcoming listing ----

    def test_upcoming_listing_carries_the_room_url(self):
        meeting = self._create_meeting(self.event)
        resp = self.client.get(self._upcoming_url())
        events = resp.json()["events"]
        matching = [e for e in events if e["uuid"] == str(self.event.uuid)]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["room_url"], _room_url(meeting))

    def test_upcoming_listing_carries_the_room_url_on_occurrences(self):
        _master, exception, meeting = self._recurring_series_with_exception("Standup")
        resp = self.client.get(self._upcoming_url())
        events = resp.json()["events"]
        matching = [e for e in events if e["title"].startswith("Standup")]
        self.assertGreaterEqual(len(matching), 2)
        for occ in matching:
            self.assertEqual(occ["room_url"], _room_url(meeting), occ["uuid"])
        self.assertIn(str(exception.uuid), [e["uuid"] for e in matching])

    def test_upcoming_listing_is_null_for_a_non_member(self):
        self._create_meeting(self.event)
        self.client.force_login(self.viewer)
        resp = self.client.get(self._upcoming_url())
        events = resp.json()["events"]
        matching = [e for e in events if e["uuid"] == str(self.event.uuid)]
        self.assertEqual(len(matching), 1)
        self.assertIsNone(matching[0]["room_url"])

    def test_membership_ignores_conversations_without_a_meeting(self):
        from workspace.calendar.recurrence import MeetingMembership
        from workspace.chat.models import Conversation, ConversationMember

        meeting = self._create_meeting(self.event)
        chat = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Lunch", created_by=self.owner
        )
        ConversationMember.objects.create(conversation=chat, user=self.member)
        membership = MeetingMembership(self.member)
        self.assertIn(meeting.conversation_id, membership)
        self.assertNotIn(chat.uuid, membership)

    # ---- query budget ----

    def _query_count(self, url):
        # Warm up first: the process' first authenticated request also fills
        # the user-settings cache and creates the presence row, and measuring
        # that run would compare a cold baseline against a warm assertion.
        self.client.get(url)
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(url)
        # A guard measuring queries on a failed request would report a bogus
        # low count and pass for the wrong reason.
        self.assertEqual(resp.status_code, 200)
        return len(ctx.captured_queries)

    def test_listing_does_not_query_membership_per_event(self):
        self._create_meeting(self.event)
        url = self._range_url()

        # Measured against a single meeting-bearing event; growing the
        # dataset below must not grow this count - membership is one set
        # resolved once per request, not one lookup per meeting.
        baseline = self._query_count(url)

        for i in range(5):
            extra = Event.objects.create(
                calendar=self.calendar,
                owner=self.owner,
                title=f"E{i}",
                start=timezone.now() + timezone.timedelta(hours=3 + i),
                end=timezone.now() + timezone.timedelta(hours=4 + i),
            )
            EventMember.objects.create(event=extra, user=self.member)
            self._create_meeting(extra)

        with self.assertNumQueries(baseline):
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            len([e for e in resp.json() if e["room_url"]]),
            6,
            "every meeting-bearing event must still carry its room url",
        )

    def test_upcoming_listing_does_not_query_membership_per_event(self):
        self._create_meeting(self.event)
        url = self._upcoming_url()
        baseline = self._query_count(url)

        for i in range(5):
            extra = Event.objects.create(
                calendar=self.calendar,
                owner=self.owner,
                title=f"E{i}",
                start=timezone.now() + timezone.timedelta(hours=3 + i),
                end=timezone.now() + timezone.timedelta(hours=4 + i),
            )
            EventMember.objects.create(event=extra, user=self.member)
            self._create_meeting(extra)

        with self.assertNumQueries(baseline):
            resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)


class EventCardRoomUrlTests(TestCase):
    """The server-rendered card offers the same door as the API payload."""

    def setUp(self):
        self.owner = User.objects.create_user("cardowner", "o@example.com", "pw")
        self.member = User.objects.create_user("cardmember", "m@example.com", "pw")
        self.viewer = User.objects.create_user("cardviewer", "v@example.com", "pw")
        self.calendar = Calendar.objects.create(owner=self.owner, name="Cal")
        self.now = timezone.now().replace(microsecond=0)
        for user in (self.member, self.viewer):
            CalendarSubscription.objects.create(user=user, calendar=self.calendar)
        self.event = Event.objects.create(
            calendar=self.calendar,
            owner=self.owner,
            title="Sync",
            start=self.now + timezone.timedelta(hours=1),
            end=self.now + timezone.timedelta(hours=2),
        )
        EventMember.objects.create(event=self.event, user=self.member)

    def test_card_offers_the_room_to_a_member(self):
        from workspace.chat.services.meetings import create_meeting

        meeting = create_meeting(self.event, self.owner)
        self.client.force_login(self.member)
        resp = self.client.get(f"/calendar/events/{self.event.pk}/card")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["room_url"], _room_url(meeting))
        self.assertContains(resp, f"/chat/room/{meeting.conversation_id}")

    def test_card_hides_the_room_from_a_non_member(self):
        from workspace.chat.services.meetings import create_meeting

        meeting = create_meeting(self.event, self.owner)
        self.client.force_login(self.viewer)
        resp = self.client.get(f"/calendar/events/{self.event.pk}/card")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.context["room_url"])
        self.assertNotContains(resp, f"/chat/room/{meeting.conversation_id}")


class EventPanelRoomButtonTests(TestCase):
    """The panel's join button is a plain link - it opens the room, it does
    not create anything (creating a meeting stays the owner's button)."""

    def test_panel_opens_the_room_without_acting_on_it(self):
        html = render_to_string("calendar/ui/partials/_event_detail_panel.html")
        marker = html.index("room_url", html.index("room_url") + 1)
        anchor = html[html.rindex("<a", 0, marker) : html.index("</a>", marker)]
        # A door, not a command: it navigates to the room the server
        # handed us, and posts nothing on the way.
        self.assertIn("target=", anchor)
        self.assertIn("noopener", anchor)
        self.assertNotIn("@click", anchor)
