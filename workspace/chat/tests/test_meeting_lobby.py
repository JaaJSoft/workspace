from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import MeetingGuest
from workspace.chat.services import calls
from workspace.chat.services.meeting_guests import resolve_guest
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import admit_guest, create_meeting

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
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(
            "/api/v1/chat/meetings", {"event_id": str(self.event.uuid)}, format="json"
        )
        self.assertEqual(resp.status_code, 403)

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

    def test_lock_returns_409_without_an_active_call(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            f"/api/v1/chat/meetings/{self.meeting.uuid}/lock",
            {"locked": True},
            format="json",
        )
        self.assertEqual(resp.status_code, 409)

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

    def test_summary_404_for_unknown_slug(self):
        resp = self.client.get("/api/v1/chat/meet/does-not-exist")
        self.assertEqual(resp.status_code, 404)

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
