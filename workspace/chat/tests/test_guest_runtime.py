from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.chat.models import CallParticipant, MeetingGuest
from workspace.chat.services import call_signaling as sig
from workspace.chat.services import calls
from workspace.chat.services.meeting_guests import issue_token
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import (
    create_meeting,
    end_meeting,
    remove_guest,
    set_locked,
)
from workspace.chat.services.participant_keys import guest_key, user_key

from .meeting_fixtures import guest_with_token, make_event

User = get_user_model()


class GuestRuntimeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="host", password="x")
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

    def _admit(self, meeting=None, occurrence_start=None, display_name="Ada"):
        return guest_with_token(
            meeting or self.meeting,
            occurrence_start or self.occurrence_start,
            display_name=display_name,
        )

    def _url(self, meeting, action):
        return f"/api/v1/chat/meet/{meeting.slug}/{action}"

    def _post(self, action, token, meeting=None):
        return self.client.post(
            self._url(meeting or self.meeting, action), HTTP_X_MEETING_TOKEN=token
        )

    def _get(self, action, token, meeting=None):
        return self.client.get(
            self._url(meeting or self.meeting, action), HTTP_X_MEETING_TOKEN=token
        )

    # --- join ---

    def test_guest_joins_active_call(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        resp = self._post("join", token)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("state", resp.data)
        self.assertIn("ice_servers", resp.data)
        participant = CallParticipant.objects.get(guest=guest)
        self.assertIsNone(participant.left_at)

    def test_join_touches_presence_under_guest_key_not_bare_uuid(self):
        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        guest, token = self._admit()
        self._post("join", token)
        presence = calls.get_presence(session.uuid)
        self.assertIn(guest_key(guest.uuid), presence)
        self.assertNotIn(str(guest.uuid), presence)

    def test_join_without_an_active_call_is_409(self):
        # Admitted, valid token, but no host has started the call yet: 404
        # is reserved for auth failures in this file, so this is a conflict,
        # not "not found".
        guest, token = self._admit()
        resp = self._post("join", token)
        self.assertEqual(resp.status_code, 409)
        self.assertIn("detail", resp.data)

    def test_join_refused_when_locked(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        set_locked(self.meeting, True)
        guest, token = self._admit()
        resp = self._post("join", token)
        self.assertEqual(resp.status_code, 423)

    def test_join_refused_when_locked_before_any_session(self):
        # Meeting-level pre-lock, no CallSession yet at all.
        set_locked(self.meeting, True)
        guest, token = self._admit()
        resp = self._post("join", token)
        self.assertEqual(resp.status_code, 423)

    def test_join_refused_when_full(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        with override_settings(CHAT_CALL_MAX_PARTICIPANTS=1):
            resp = self._post("join", token)
        self.assertEqual(resp.status_code, 409)

    def test_join_with_unknown_token_is_404(self):
        resp = self._post("join", "not-a-real-token")
        self.assertEqual(resp.status_code, 404)

    def test_join_with_no_token_is_404(self):
        resp = self._post("join", "")
        self.assertEqual(resp.status_code, 404)

    # --- slug scoping (the shared auth helper) ---

    def test_token_for_another_meeting_is_refused(self):
        other_owner = User.objects.create_user(username="other-host", password="x")
        other_event = make_event(other_owner)
        other_meeting = create_meeting(other_event, other_owner)
        other_occurrence = current_occurrence(other_meeting)[0]
        _guest, token = self._admit(
            meeting=other_meeting, occurrence_start=other_occurrence
        )

        # This token names a guest of other_meeting; used against self.meeting's
        # slug it must be refused, not silently authorize the wrong meeting.
        resp = self._post("join", token, meeting=self.meeting)
        self.assertEqual(resp.status_code, 404)

    def test_removed_guests_token_stops_working_mid_session(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        self.assertEqual(self._post("join", token).status_code, 200)

        remove_guest(guest)

        resp = self._post("heartbeat", token)
        self.assertEqual(resp.status_code, 404)

    # --- leave ---

    def test_leave_marks_participant_left_and_drops_presence(self):
        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        guest, token = self._admit()
        self._post("join", token)

        resp = self._post("leave", token)
        self.assertEqual(resp.status_code, 200)

        participant = CallParticipant.objects.get(guest=guest)
        self.assertIsNotNone(participant.left_at)
        self.assertNotIn(guest_key(guest.uuid), calls.get_presence(session.uuid))

    def test_leave_with_no_active_call_is_a_no_op(self):
        guest, token = self._admit()
        resp = self._post("leave", token)
        self.assertEqual(resp.status_code, 200)

    # --- heartbeat ---

    def test_heartbeat_writes_presence_under_guest_key(self):
        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        guest, token = self._admit()
        self._post("join", token)

        resp = self.client.post(
            self._url(self.meeting, "heartbeat"),
            {"media_state": {"audio": False}},
            format="json",
            HTTP_X_MEETING_TOKEN=token,
        )
        self.assertEqual(resp.status_code, 200)
        presence = calls.get_presence(session.uuid)
        self.assertEqual(presence[guest_key(guest.uuid)], {"audio": False})
        self.assertNotIn(str(guest.uuid), presence)

    def test_heartbeat_without_an_active_call_is_409(self):
        guest, token = self._admit()
        resp = self._post("heartbeat", token)
        self.assertEqual(resp.status_code, 409)

    def test_heartbeat_before_joining_the_call_is_refused(self):
        # A heartbeat writes presence and fans out call_participant_updated,
        # at 120/min, for a participant table this guest is not in.
        # MeetingGuestSignalView already requires the row; so must this.
        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        guest, token = self._admit()

        resp = self._post("heartbeat", token)

        self.assertEqual(resp.status_code, 400)
        self.assertNotIn(guest_key(guest.uuid), calls.get_presence(session.uuid))

    def test_heartbeat_drops_unknown_media_state_keys(self):
        # I-3: request.data is anonymous input merged verbatim into the
        # shared per-session presence cache and rebroadcast to every peer -
        # only the known boolean flags may survive.
        session, _, _ = calls.start_or_join_call(
            self.owner, self.meeting.conversation_id
        )
        guest, token = self._admit()
        self._post("join", token)

        resp = self.client.post(
            self._url(self.meeting, "heartbeat"),
            {"media_state": {"audio": False, "evil": "<script>", "screen": True}},
            format="json",
            HTTP_X_MEETING_TOKEN=token,
        )
        self.assertEqual(resp.status_code, 200)
        presence = calls.get_presence(session.uuid)
        self.assertEqual(
            presence[guest_key(guest.uuid)], {"audio": False, "screen": True}
        )

    def test_heartbeat_at_realistic_cadence_is_not_throttled(self):
        # I-2: call.js heartbeats every 5s per participant, about 12/min,
        # which must not trip the shared 30/min public scope - especially
        # with more than one guest behind the same IP.
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        self._post("join", token)

        for _ in range(40):
            resp = self._post("heartbeat", token)
            self.assertEqual(resp.status_code, 200)

    # --- state ---

    def test_state_for_waiting_guest_is_lobby_only(self):
        token, token_hash = issue_token()
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start,
            token_hash=token_hash,
        )
        resp = self._get("state", token)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["admitted"])
        self.assertEqual(resp.data["state"], MeetingGuest.State.WAITING)
        keys = set(resp.data.keys())
        for forbidden in ("title", "conversation_id", "conversation", "participants"):
            self.assertNotIn(forbidden, keys)

    def test_state_for_a_waiting_guest_of_a_past_occurrence_is_ended(self):
        # The stream 404s this same token (its WAITING gate checks the
        # occurrence), so /state reporting "waiting" forever told the guest
        # to keep waiting for a lobby nobody is watching any more.
        token, token_hash = issue_token()
        MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.WAITING,
            occurrence_start=self.occurrence_start - timedelta(weeks=1),
            token_hash=token_hash,
        )
        resp = self._get("state", token)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, {"admitted": False, "state": "ended"})

    def test_state_for_admitted_guest_with_no_active_call(self):
        guest, token = self._admit()
        resp = self._get("state", token)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["admitted"])
        self.assertFalse(resp.data["active"])

    def test_state_for_admitted_guest_in_an_active_call(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        self._post("join", token)

        resp = self._get("state", token)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["admitted"])
        self.assertTrue(resp.data["active"])
        self.assertIn("ice_servers", resp.data)
        self.assertNotIn("conversation_id", resp.data)

    def test_state_with_unknown_token_is_404(self):
        resp = self._get("state", "not-a-real-token")
        self.assertEqual(resp.status_code, 404)

    def test_state_after_host_ends_meeting_does_not_say_admitted(self):
        # I-4 regression: end_meeting only sweeps WAITING rows to REFUSED, so
        # an ADMITTED guest's row survives its own occurrence closing
        # verbatim. /state must not keep telling that guest they are
        # admitted forever.
        guest, token = self._admit()
        end_meeting(self.meeting)

        resp = self._get("state", token)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["admitted"])
        self.assertNotEqual(resp.data.get("state"), "admitted")


class GuestSignalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="host", password="x")
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

    def _admit(self, meeting=None, occurrence_start=None, display_name="Ada"):
        return guest_with_token(
            meeting or self.meeting,
            occurrence_start or self.occurrence_start,
            display_name=display_name,
        )

    def _url(self, meeting=None):
        return f"/api/v1/chat/meet/{(meeting or self.meeting).slug}/signal"

    def _signal(self, token, to_participant, signal=None, meeting=None):
        return self.client.post(
            self._url(meeting),
            {"to_participant": to_participant, "signal": signal or {"type": "offer"}},
            format="json",
            HTTP_X_MEETING_TOKEN=token,
        )

    def test_guest_can_signal_a_member(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        self._post_join(token)
        sig.drain_events(user_key(self.owner.id))  # clear lifecycle noise

        resp = self._signal(token, user_key(self.owner.id))
        self.assertEqual(resp.status_code, 200)

        delivered = [
            e
            for e in sig.drain_events(user_key(self.owner.id))
            if e["event"] == "call_signal"
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(
            delivered[0]["data"]["from_participant"], guest_key(guest.uuid)
        )

    def test_guest_can_signal_another_guest(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest_a, token_a = self._admit(display_name="Ada")
        guest_b, token_b = self._admit(display_name="Bea")
        self._post_join(token_a)
        self._post_join(token_b)
        sig.drain_events(guest_key(guest_b.uuid))  # clear lifecycle noise

        resp = self._signal(token_a, guest_key(guest_b.uuid))
        self.assertEqual(resp.status_code, 200)

        delivered = [
            e
            for e in sig.drain_events(guest_key(guest_b.uuid))
            if e["event"] == "call_signal"
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(
            delivered[0]["data"]["from_participant"], guest_key(guest_a.uuid)
        )

    def test_signal_to_a_participant_of_a_different_session_is_refused(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        self._post_join(token)

        other_owner = User.objects.create_user(username="other-host", password="x")
        other_event = make_event(other_owner)
        other_meeting = create_meeting(other_event, other_owner)
        calls.start_or_join_call(other_owner, other_meeting.conversation_id)

        resp = self._signal(token, user_key(other_owner.id))
        self.assertEqual(resp.status_code, 400)

    def test_signal_rejects_a_non_canonical_member_key(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        self._post_join(token)

        resp = self._signal(token, "u:007")
        self.assertEqual(resp.status_code, 400)

    def test_signal_rejects_a_non_canonical_guest_key(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest_a, token_a = self._admit(display_name="Ada")
        guest_b, _token_b = self._admit(display_name="Bea")
        self._post_join(token_a)

        resp = self._signal(token_a, f"g:{str(guest_b.uuid).upper()}")
        self.assertEqual(resp.status_code, 400)

    def test_guest_who_has_not_joined_the_call_cannot_signal(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        # Deliberately no join call here.

        resp = self._signal(token, user_key(self.owner.id))
        self.assertEqual(resp.status_code, 400)

    def test_signal_with_no_active_call_is_refused(self):
        guest, token = self._admit()

        resp = self._signal(token, user_key(self.owner.id))
        self.assertEqual(resp.status_code, 400)

    def test_signal_burst_is_not_throttled_by_the_shared_public_scope(self):
        # I-4: a realistic full-room join bursts about 85 requests (5 peers
        # x ~15 ICE candidates, plus offer/answer) within the first seconds -
        # drive close to that, not just past the old 30/min scope, so a
        # future tightening of the new scope trips this test rather than
        # shipping silently.
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest, token = self._admit()
        self._post_join(token)

        for _ in range(100):
            resp = self._signal(token, user_key(self.owner.id))
            self.assertEqual(resp.status_code, 200)

    def _post_join(self, token, meeting=None):
        meeting = meeting or self.meeting
        return self.client.post(
            f"/api/v1/chat/meet/{meeting.slug}/join", HTTP_X_MEETING_TOKEN=token
        )
