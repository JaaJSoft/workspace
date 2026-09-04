from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import CallSession, MeetingGuest
from workspace.chat.services import calls
from workspace.chat.services.meeting_guests import resolve_guest
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import (
    admit_guest,
    create_meeting,
    end_meeting,
    set_locked,
)

User = get_user_model()


def make_event(owner, start=None, end=None):
    cal = Calendar.objects.create(name="Cal", owner=owner)
    return Event.objects.create(
        calendar=cal,
        owner=owner,
        title="Standup",
        start=start or timezone.now(),
        end=end,
    )


class MeetingHostViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="host", password="x")
        self.outsider = User.objects.create_user(username="out", password="x")
        now = timezone.now()
        self.event = make_event(
            self.owner,
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=25),
        )
        self.meeting = create_meeting(self.event, self.owner)
        self.occurrence_start = current_occurrence(self.meeting, now=now)[0]
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    def _guest(self, state=MeetingGuest.State.WAITING, token_hash="a" * 64):
        return MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=state,
            occurrence_start=self.occurrence_start,
            token_hash=token_hash,
        )

    # --- create ---

    def test_owner_creates_meeting(self):
        self.client.force_authenticate(self.owner)
        other_event = make_event(self.owner)
        resp = self.client.post(
            "/api/v1/chat/meetings", {"event_id": str(other_event.uuid)}, format="json"
        )
        self.assertEqual(resp.status_code, 201)
        self.assertIn("join_url", resp.data)
        self.assertTrue(resp.data["join_url"].endswith(f"/meet/{resp.data['slug']}"))

    def test_create_is_idempotent_for_the_owner(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            "/api/v1/chat/meetings", {"event_id": str(self.event.uuid)}, format="json"
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["uuid"], str(self.meeting.uuid))

    def test_non_owner_cannot_create(self):
        # 404, not 403: an event that exists but belongs to someone else
        # must look the same as one that does not exist at all, matching
        # every other route in this file.
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(
            "/api/v1/chat/meetings", {"event_id": str(self.event.uuid)}, format="json"
        )
        self.assertEqual(resp.status_code, 404)

    def test_unknown_event_id_is_404(self):
        import uuid

        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            "/api/v1/chat/meetings", {"event_id": str(uuid.uuid4())}, format="json"
        )
        self.assertEqual(resp.status_code, 404)

    def test_malformed_event_id_is_400(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            "/api/v1/chat/meetings", {"event_id": "not-a-uuid"}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    # --- lobby ---

    def test_lobby_lists_only_waiting_guests(self):
        waiting = self._guest(state=MeetingGuest.State.WAITING, token_hash="b" * 64)
        self._guest(state=MeetingGuest.State.ADMITTED, token_hash="c" * 64)
        self._guest(state=MeetingGuest.State.REFUSED, token_hash="d" * 64)
        self.client.force_authenticate(self.owner)
        resp = self.client.get(f"/api/v1/chat/meetings/{self.meeting.uuid}/lobby")
        self.assertEqual(resp.status_code, 200)
        uuids = {g["uuid"] for g in resp.data}
        self.assertEqual(uuids, {str(waiting.uuid)})

    def test_lobby_excludes_waiting_guests_from_a_past_occurrence(self):
        # Same meeting, different occurrence_start: a leftover WAITING row
        # from a prior week's standup must not show up as if it were
        # actionable today - admitting it would produce a guest
        # resolve_guest can never let in.
        current = self._guest(token_hash="l" * 64)
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Stale",
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start - timedelta(days=7),
            token_hash="m" * 64,
        )
        self.client.force_authenticate(self.owner)
        resp = self.client.get(f"/api/v1/chat/meetings/{self.meeting.uuid}/lobby")
        self.assertEqual(resp.status_code, 200)
        uuids = {g["uuid"] for g in resp.data}
        self.assertEqual(uuids, {str(current.uuid)})

    def test_lobby_404_for_non_member(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.get(f"/api/v1/chat/meetings/{self.meeting.uuid}/lobby")
        self.assertEqual(resp.status_code, 404)

    # --- admit / refuse / remove ---

    def test_admit_flips_state(self):
        guest = self._guest(token_hash="e" * 64)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{guest.uuid}/admit"
        )
        self.assertEqual(resp.status_code, 200)
        guest.refresh_from_db()
        self.assertEqual(guest.state, MeetingGuest.State.ADMITTED)

    def test_admit_404_for_non_member(self):
        guest = self._guest(token_hash="f" * 64)
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{guest.uuid}/admit"
        )
        self.assertEqual(resp.status_code, 404)

    def test_refuse_flips_state(self):
        guest = self._guest(token_hash="g" * 64)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{guest.uuid}/refuse"
        )
        self.assertEqual(resp.status_code, 200)
        guest.refresh_from_db()
        self.assertEqual(guest.state, MeetingGuest.State.REFUSED)

    def test_refuse_404_for_non_member(self):
        guest = self._guest(token_hash="h" * 64)
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{guest.uuid}/refuse"
        )
        self.assertEqual(resp.status_code, 404)

    def test_remove_flips_state(self):
        guest = self._guest(state=MeetingGuest.State.ADMITTED, token_hash="i" * 64)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{guest.uuid}/remove"
        )
        self.assertEqual(resp.status_code, 200)
        guest.refresh_from_db()
        self.assertEqual(guest.state, MeetingGuest.State.REMOVED)

    def test_remove_404_for_non_member(self):
        guest = self._guest(state=MeetingGuest.State.ADMITTED, token_hash="j" * 64)
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{guest.uuid}/remove"
        )
        self.assertEqual(resp.status_code, 404)

    def test_admit_404_for_unknown_guest(self):
        import uuid

        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{uuid.uuid4()}/admit"
        )
        self.assertEqual(resp.status_code, 404)

    # --- lock ---

    def test_lock_before_any_session_persists_to_the_meeting(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/lock",
            {"locked": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.locked_occurrence_start, self.occurrence_start)

    def test_pre_lock_carries_over_to_the_session_created_on_join(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/lock",
            {"locked": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        self.assertTrue(session.locked)

    def test_lock_locks_the_active_call(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/lock",
            {"locked": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        session = calls.get_active_call(self.meeting.conversation_id)
        self.assertTrue(session.locked)

    def test_lock_uses_is_truthy_not_python_truthiness(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/lock",
            {"locked": "false"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        session = calls.get_active_call(self.meeting.conversation_id)
        self.assertFalse(session.locked)

    def test_lock_404_for_non_member(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/lock",
            {"locked": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_lock_does_not_carry_into_the_next_occurrence(self):
        # I-1 regression: the durable lock must be scoped to the occurrence
        # it was set during. Locking this week's standup must not 423 next
        # week's guests once the host has ended this occurrence.
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        set_locked(self.meeting, True)
        self.assertTrue(
            calls.is_call_locked(self.meeting.conversation_id, self.occurrence_start)
        )

        end_meeting(self.meeting)

        self.meeting.refresh_from_db()
        self.assertIsNone(self.meeting.locked_occurrence_start)
        self.assertFalse(
            calls.is_call_locked(self.meeting.conversation_id, self.occurrence_start)
        )

    def test_a_lock_nobody_ever_ended_does_not_reach_the_next_occurrence(self):
        # I-1, the elapsed half: a host pre-locks an empty room, no call is
        # ever started and nobody presses End, so no lifecycle path runs to
        # clear the flag - the occurrence simply elapses. Next week's guests
        # must still get in.
        now = timezone.now()
        recurring_event = Event.objects.create(
            calendar=Calendar.objects.create(name="Weekly", owner=self.owner),
            owner=self.owner,
            title="Standup",
            start=now - timedelta(weeks=3, minutes=5),
            end=now - timedelta(weeks=3) + timedelta(minutes=25),
            recurrence_frequency=Event.RecurrenceFrequency.WEEKLY,
            recurrence_interval=1,
        )
        meeting = create_meeting(recurring_event, self.owner)
        set_locked(meeting, True)

        knock_url = f"/api/v1/chat/meet/{meeting.slug}/knock"
        this_week = self.client.post(knock_url, {"display_name": "Ada"}, format="json")
        self.assertEqual(this_week.status_code, 423)

        with patch(
            "workspace.chat.services.meeting_occurrences.timezone.now",
            return_value=now + timedelta(weeks=1),
        ):
            next_week = self.client.post(
                knock_url, {"display_name": "Bo"}, format="json"
            )
        self.assertEqual(next_week.status_code, 201)

    # --- end ---

    def test_end_sets_closed_occurrence_start(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/v1/chat/meetings/{self.meeting.uuid}/end")
        self.assertEqual(resp.status_code, 200)
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.closed_occurrence_start, self.occurrence_start)

    def test_end_404_for_non_member(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(f"/api/v1/chat/meetings/{self.meeting.uuid}/end")
        self.assertEqual(resp.status_code, 404)

    # --- anonymous access: every host route must 403 ---

    def test_anonymous_cannot_reach_any_host_route(self):
        guest = self._guest(token_hash="k" * 64)
        routes = [
            ("post", "/api/v1/chat/meetings", {"event_id": str(self.event.uuid)}),
            ("get", f"/api/v1/chat/meetings/{self.meeting.uuid}/lobby", None),
            (
                "post",
                f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{guest.uuid}/admit",
                None,
            ),
            (
                "post",
                f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{guest.uuid}/refuse",
                None,
            ),
            (
                "post",
                f"/api/v1/chat/meetings/{self.meeting.uuid}/guests/{guest.uuid}/remove",
                None,
            ),
            (
                "post",
                f"/api/v1/chat/meetings/{self.meeting.uuid}/lock",
                {"locked": True},
            ),
            ("post", f"/api/v1/chat/meetings/{self.meeting.uuid}/end", None),
        ]
        self.assertEqual(len(routes), 7)
        for method, url, body in routes:
            with self.subTest(url=url):
                if body is not None:
                    resp = getattr(self.client, method)(url, body, format="json")
                else:
                    resp = getattr(self.client, method)(url)
                self.assertEqual(resp.status_code, 403)


class MeetingPublicViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="pub-host", password="x")
        self.viewer = User.objects.create_user(username="pub-viewer", password="x")
        now = timezone.now()
        self.event = make_event(
            self.owner,
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=25),
        )
        self.meeting = create_meeting(self.event, self.owner)
        self.occurrence_start = current_occurrence(self.meeting, now=now)[0]
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    # --- summary ---

    def test_summary_returns_title_start_and_locked(self):
        resp = self.client.get(f"/api/v1/chat/meet/{self.meeting.slug}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["title"], self.event.title)
        self.assertIn("start", resp.data)
        self.assertIn("locked", resp.data)
        self.assertFalse(resp.data["locked"])

    def test_summary_has_no_participants_conversation_or_guest_list(self):
        resp = self.client.get(f"/api/v1/chat/meet/{self.meeting.slug}")
        self.assertEqual(resp.status_code, 200)
        keys = set(resp.data.keys())
        for forbidden in ("participants", "conversation", "conversation_id", "guests"):
            self.assertNotIn(forbidden, keys)

    def test_summary_reflects_locked_call(self):
        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        session.locked = True
        session.save(update_fields=["locked"])
        resp = self.client.get(f"/api/v1/chat/meet/{self.meeting.slug}")
        self.assertTrue(resp.data["locked"])

    def test_summary_does_not_end_a_stale_call(self):
        # get_active_call self-heals a call whose participants have no live
        # heartbeat by ending it (write + call_ended broadcast). The public
        # summary endpoint must not trigger that off a bare, unauthenticated
        # GET - it only reads the locked flag.
        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        cache.clear()  # wipes every heartbeat -> the participant looks stale

        resp = self.client.get(f"/api/v1/chat/meet/{self.meeting.slug}")

        self.assertEqual(resp.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.state, CallSession.State.ACTIVE)

    def test_summary_404_for_unknown_slug(self):
        resp = self.client.get("/api/v1/chat/meet/does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_summary_reports_the_current_occurrence_not_the_series_start(self):
        # Weekly series that started three weeks ago; today's instance is
        # live. The series master start (event.start) is a different value
        # from what is actually reachable right now, which is the whole
        # reason current_occurrence exists - this endpoint must resolve it
        # the same way the knock endpoint does, not read event.start raw.
        now = timezone.now()
        recurring_event = Event.objects.create(
            calendar=Calendar.objects.create(name="Weekly", owner=self.owner),
            owner=self.owner,
            title="Standup",
            start=now - timedelta(weeks=3, minutes=5),
            end=now - timedelta(weeks=3) + timedelta(minutes=25),
            recurrence_frequency=Event.RecurrenceFrequency.WEEKLY,
            recurrence_interval=1,
        )
        recurring_meeting = create_meeting(recurring_event, self.owner)
        expected_start = current_occurrence(recurring_meeting, now=now)[0]
        self.assertNotEqual(expected_start, recurring_event.start)

        resp = self.client.get(f"/api/v1/chat/meet/{recurring_meeting.slug}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["start"], expected_start)

    def test_summary_identical_for_authenticated_and_anonymous(self):
        anon = self.client.get(f"/api/v1/chat/meet/{self.meeting.slug}")
        self.client.force_authenticate(self.viewer)
        authed = self.client.get(f"/api/v1/chat/meet/{self.meeting.slug}")
        self.assertEqual(anon.status_code, authed.status_code)
        self.assertEqual(anon.data, authed.data)

    # --- knock ---

    def test_knock_returns_token_and_creates_waiting_guest(self):
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(resp.data["token"])
        self.assertEqual(resp.data["state"], "waiting")
        self.assertEqual(resp.data["display_name"], "Ada")
        guest = MeetingGuest.objects.get(meeting=self.meeting)
        self.assertEqual(guest.state, MeetingGuest.State.WAITING)
        self.assertEqual(guest.occurrence_start, self.occurrence_start)

    def test_knock_strips_bidi_override_from_display_name(self):
        # A raw display name is shown verbatim to a host in the admit
        # prompt. An unstripped right-to-left override could visually
        # rewrite it - the obvious impersonation angle for a field nobody
        # authenticates before submitting. Built from chr() rather than a
        # literal so the override character in this test file is a code
        # point, not an invisible byte in the source.
        rtl_override = chr(0x202E)
        name = f"Alice{rtl_override}ecilA"
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": name},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertNotIn(rtl_override, resp.data["display_name"])
        guest = MeetingGuest.objects.get(meeting=self.meeting)
        self.assertNotIn(rtl_override, guest.display_name)

    def test_knock_token_does_not_resolve_until_admitted(self):
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
        )
        token = resp.data["token"]
        self.assertIsNone(resolve_guest(token))
        guest = MeetingGuest.objects.get(meeting=self.meeting)
        admit_guest(guest, self.owner)
        self.assertIsNotNone(resolve_guest(token))

    def test_knock_outside_occurrence_window_is_404(self):
        future_event = make_event(
            self.owner,
            start=timezone.now() + timedelta(days=10),
            end=timezone.now() + timedelta(days=10, minutes=30),
        )
        future_meeting = create_meeting(future_event, self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meet/{future_meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    def test_knock_returns_423_when_locked(self):
        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        session.locked = True
        session.save(update_fields=["locked"])
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
        )
        self.assertEqual(resp.status_code, 423)

    def test_knock_returns_423_when_meeting_locked_before_any_session(self):
        # Pre-locking (MeetingLockView, before anyone has joined) writes only
        # the meeting's durable lock - there is no CallSession yet.
        # is_call_locked must still surface that as a 423 on the knock, not
        # treat "no session" as "not locked".
        set_locked(self.meeting, True)
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
        )
        self.assertEqual(resp.status_code, 423)

    def test_knock_does_not_end_a_stale_call(self):
        # Same self-heal hazard as the summary endpoint above, but on the
        # POST path: a knock must not end a stale call as a side effect of
        # checking whether it is locked.
        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        cache.clear()

        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
        )

        self.assertEqual(resp.status_code, 201)
        session.refresh_from_db()
        self.assertEqual(session.state, CallSession.State.ACTIVE)

    def test_knock_blank_display_name_is_400(self):
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": ""},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_knock_missing_display_name_is_400(self):
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock", {}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_knock_display_name_too_long_is_400_not_500(self):
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "x" * 81},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_eleventh_knock_from_one_ip_is_rate_limited(self):
        for _ in range(10):
            resp = self.client.post(
                f"/api/v1/chat/meet/{self.meeting.slug}/knock",
                {"display_name": "Ada"},
                format="json",
            )
            self.assertEqual(resp.status_code, 201)
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
        )
        self.assertEqual(resp.status_code, 429)

    def test_rate_limit_survives_a_forged_x_forwarded_for(self):
        # NUM_PROXIES is unset in tests (the default), so the header must be
        # ignored entirely and REMOTE_ADDR - which the test client keeps
        # fixed - used instead. A distinct forged value per request proves
        # the limit isn't handing out a fresh bucket for each one.
        for i in range(10):
            resp = self.client.post(
                f"/api/v1/chat/meet/{self.meeting.slug}/knock",
                {"display_name": "Ada"},
                format="json",
                HTTP_X_FORWARDED_FOR=f"203.0.113.{i}",
            )
            self.assertEqual(resp.status_code, 201)
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
            HTTP_X_FORWARDED_FOR="203.0.113.99",
        )
        self.assertEqual(resp.status_code, 429)

    def test_stale_waiting_guest_from_past_occurrence_does_not_hold_a_slot(self):
        # A WAITING row left over from a past occurrence of the same series
        # (same meeting, different occurrence_start) must not count against
        # the current occurrence's cap - nothing ever purges these rows, so
        # an unscoped count would eventually block every future occurrence.
        with override_settings(MEETING_MAX_WAITING_GUESTS=1):
            MeetingGuest.objects.create(
                meeting=self.meeting,
                display_name="Stale",
                occurrence_start=self.occurrence_start - timedelta(days=7),
                token_hash="s" * 64,
            )
            resp = self.client.post(
                f"/api/v1/chat/meet/{self.meeting.slug}/knock",
                {"display_name": "Ada"},
                format="json",
            )
        self.assertEqual(resp.status_code, 201)

    def test_knock_after_end_meeting_is_refused(self):
        end_meeting(self.meeting)
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)

    @override_settings(MEETING_MAX_WAITING_GUESTS=2)
    def test_knock_429_once_lobby_is_full_even_from_different_ips(self):
        for i in range(2):
            resp = self.client.post(
                f"/api/v1/chat/meet/{self.meeting.slug}/knock",
                {"display_name": f"Guest{i}"},
                format="json",
                REMOTE_ADDR=f"10.0.0.{i}",
            )
            self.assertEqual(resp.status_code, 201)
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Guest2"},
            format="json",
            REMOTE_ADDR="10.0.0.99",
        )
        self.assertEqual(resp.status_code, 429)

    def test_knock_identical_for_authenticated_and_anonymous(self):
        anon = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Ada"},
            format="json",
            REMOTE_ADDR="10.1.1.1",
        )
        self.client.force_authenticate(self.viewer)
        authed = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Bob"},
            format="json",
            REMOTE_ADDR="10.1.1.2",
        )
        self.assertEqual(anon.status_code, authed.status_code)
        self.assertEqual(set(anon.data.keys()), set(authed.data.keys()))
