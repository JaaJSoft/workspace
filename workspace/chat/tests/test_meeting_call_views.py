from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.calendar.models import EventMember
from workspace.chat.models import CallParticipant
from workspace.chat.services import call_signaling as sig
from workspace.chat.services import calls
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import create_meeting
from workspace.chat.services.participant_keys import guest_key, user_key

from .meeting_fixtures import guest_with_token, make_event

User = get_user_model()


class MeetingCallViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.host = User.objects.create_user(username="mc-host", password="x")
        self.cohost = User.objects.create_user(username="mc-cohost", password="x")
        self.outsider = User.objects.create_user(username="mc-out", password="x")
        now = timezone.now()
        self.event = make_event(
            self.host, start=now - timedelta(minutes=5), end=now + timedelta(minutes=25)
        )
        EventMember.objects.create(event=self.event, user=self.cohost)
        self.meeting = create_meeting(self.event, self.host)
        self.occurrence_start = current_occurrence(self.meeting, now=now)[0]
        self.client = APIClient()
        self.base = f"/api/v1/chat/meetings/{self.meeting.uuid}/call"

    def tearDown(self):
        cache.clear()

    def test_state_is_inactive_before_anyone_joins(self):
        self.client.force_authenticate(self.host)
        resp = self.client.get(self.base)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.data["active"])

    def test_outsider_is_404_everywhere_but_leave(self):
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.assertEqual(self.client.post(f"{self.base}/join").status_code, 404)
        self.assertEqual(self.client.post(f"{self.base}/heartbeat").status_code, 404)
        self.assertEqual(self.client.post(f"{self.base}/leave").status_code, 200)

    def test_host_join_creates_the_meeting_call(self):
        self.client.force_authenticate(self.host)
        resp = self.client.post(f"{self.base}/join")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["state"]["meeting_id"], str(self.meeting.uuid))
        self.assertIsNone(resp.data["state"]["conversation_id"])
        self.assertTrue(calls.active_call_session(calls.MeetingScope(self.meeting)))

    def test_cohost_learns_of_the_call_and_can_signal_the_host(self):
        self.client.force_authenticate(self.host)
        self.client.post(f"{self.base}/join")
        events = [e["event"] for e in sig.drain_events(user_key(self.cohost.id))]
        self.assertIn("call_started", events)
        self.client.force_authenticate(self.cohost)
        self.client.post(f"{self.base}/join")
        resp = self.client.post(
            f"{self.base}/signal",
            {"to_participant": user_key(self.host.id), "signal": {"type": "offer"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            any(
                e["event"] == "call_signal"
                for e in sig.drain_events(user_key(self.host.id))
            )
        )

    def test_signal_to_a_stranger_or_absent_guest_is_400(self):
        self.client.force_authenticate(self.host)
        self.client.post(f"{self.base}/join")
        guest, _ = guest_with_token(self.meeting, self.occurrence_start)
        for target in (user_key(self.outsider.id), guest_key(guest.uuid)):
            resp = self.client.post(
                f"{self.base}/signal",
                {"to_participant": target, "signal": {"type": "offer"}},
                format="json",
            )
            self.assertEqual(resp.status_code, 400, target)

    def test_heartbeat_updates_presence(self):
        self.client.force_authenticate(self.host)
        self.client.post(f"{self.base}/join")
        resp = self.client.post(
            f"{self.base}/heartbeat", {"media_state": {"audio": False}}, format="json"
        )
        self.assertEqual(resp.status_code, 200)
        session = calls.active_call_session(calls.MeetingScope(self.meeting))
        self.assertEqual(
            calls.get_presence(session.uuid)[user_key(self.host.id)]["audio"], False
        )

    def test_leave_ends_an_empty_call(self):
        self.client.force_authenticate(self.host)
        self.client.post(f"{self.base}/join")
        self.assertEqual(self.client.post(f"{self.base}/leave").status_code, 200)
        self.assertIsNone(calls.active_call_session(calls.MeetingScope(self.meeting)))
        self.assertEqual(
            CallParticipant.objects.filter(left_at__isnull=True).count(), 0
        )
