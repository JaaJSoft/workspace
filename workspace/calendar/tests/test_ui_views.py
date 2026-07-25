"""Tests for workspace.calendar.ui.views."""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.calendar.models import Calendar
from workspace.users.services.settings import set_setting

User = get_user_model()


class CalendarIndexViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="caluser",
            password="pass123",
        )

    def tearDown(self):
        cache.clear()

    def test_requires_login(self):
        resp = self.client.get("/calendar")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url)

    def test_renders_for_authenticated_user(self):
        self.client.login(username="caluser", password="pass123")
        resp = self.client.get("/calendar")
        self.assertEqual(resp.status_code, 200)

    def test_creates_default_calendar_if_user_has_none(self):
        self.assertFalse(Calendar.objects.filter(owner=self.user).exists())
        self.client.login(username="caluser", password="pass123")
        self.client.get("/calendar")
        self.assertTrue(
            Calendar.objects.filter(owner=self.user, name="Personal").exists()
        )

    def test_context_has_calendars(self):
        self.client.login(username="caluser", password="pass123")
        resp = self.client.get("/calendar")
        self.assertIn("calendars", resp.context)
        data = resp.context["calendars"]
        self.assertIn("owned", data)
        self.assertIn("subscribed", data)
        # Default 'Personal' calendar created on first hit:
        self.assertEqual(len(data["owned"]), 1)
        self.assertEqual(data["owned"][0]["name"], "Personal")

    # ── prefs — server-rendered to avoid double-fetch on init ──
    # The view passes the raw dict; the template renders it via |json_script
    # into <script id="calendar-prefs-data" type="application/json">.

    def test_context_has_prefs_empty_dict_when_no_prefs_stored(self):
        self.client.login(username="caluser", password="pass123")
        resp = self.client.get("/calendar")
        self.assertIn("prefs", resp.context)
        self.assertEqual(resp.context["prefs"], {})

    def test_context_prefs_reflects_stored_settings(self):
        set_setting(
            self.user,
            "calendar",
            "preferences",
            {
                "defaultView": "agenda",
                "firstDay": 0,
                "weekNumbers": True,
                "timeFormat": "12h",
            },
        )
        self.client.login(username="caluser", password="pass123")
        resp = self.client.get("/calendar")
        prefs = resp.context["prefs"]
        self.assertEqual(prefs["defaultView"], "agenda")
        self.assertEqual(prefs["firstDay"], 0)
        self.assertTrue(prefs["weekNumbers"])
        self.assertEqual(prefs["timeFormat"], "12h")

    def test_prefs_rendered_as_json_script_tag(self):
        """End-to-end: |json_script must inject a parseable <script> tag with the prefs."""
        set_setting(self.user, "calendar", "preferences", {"defaultView": "agenda"})
        self.client.login(username="caluser", password="pass123")
        resp = self.client.get("/calendar")
        self.assertContains(
            resp,
            '<script id="calendar-prefs-data" type="application/json">',
        )
        self.assertContains(resp, '"defaultView": "agenda"')


class EventCardMembersTests(TestCase):
    """Pins the event card's attendees/invite-status contract and its
    member query count (regression for the bypassed members prefetch)."""

    def setUp(self):
        from django.utils import timezone

        from workspace.calendar.models import Event, EventMember

        self.owner = User.objects.create_user(username="cardowner", password="pass123")
        self.viewer = User.objects.create_user(
            username="cardviewer", password="pass123"
        )
        self.cal = Calendar.objects.create(name="Card Cal", owner=self.owner)
        self.event = Event.objects.create(
            calendar=self.cal,
            title="Board meeting",
            owner=self.owner,
            start=timezone.now(),
            end=timezone.now(),
        )
        self.members = []
        for i in range(6):
            u = User.objects.create_user(username=f"attendee{i}", password="pass123")
            self.members.append(
                EventMember.objects.create(
                    event=self.event, user=u, status=EventMember.Status.ACCEPTED
                )
            )
        self.declined_user = User.objects.create_user(
            username="decliner", password="pass123"
        )
        EventMember.objects.create(
            event=self.event,
            user=self.declined_user,
            status=EventMember.Status.DECLINED,
        )
        from workspace.calendar.models import EventMember as EM

        self.viewer_membership = EM.objects.create(
            event=self.event, user=self.viewer, status=EM.Status.PENDING
        )

    def _url(self):
        return f"/calendar/events/{self.event.pk}/card"

    def test_attendees_exclude_declined_and_cap_at_five(self):
        self.client.login(username="cardowner", password="pass123")
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        attendees = resp.context["attendees"]
        self.assertEqual(len(attendees), 5)
        self.assertNotIn(self.declined_user.id, [m.user_id for m in attendees])

    def test_invite_status_for_member_viewer(self):
        from workspace.calendar.models import EventMember

        self.client.login(username="cardviewer", password="pass123")
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["invite_status"], EventMember.Status.PENDING)

    def test_invite_status_none_for_owner(self):
        self.client.login(username="cardowner", password="pass123")
        resp = self.client.get(self._url())
        self.assertIsNone(resp.context["invite_status"])

    def test_members_fetched_in_a_single_query(self):
        """The card must serve attendees and the viewer's membership from
        the prefetched member set, not fresh per-need queries."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.login(username="cardviewer", password="pass123")
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        member_queries = [
            q for q in ctx.captured_queries if "calendar_eventmember" in q["sql"]
        ]
        # 2 = the event lookup itself (its visibility filter embeds a
        # membership subquery) + the members prefetch. The attendees list
        # and the viewer's membership must not add queries of their own.
        self.assertEqual(
            len(member_queries),
            2,
            f"expected 2 member queries, got {len(member_queries)}",
        )
