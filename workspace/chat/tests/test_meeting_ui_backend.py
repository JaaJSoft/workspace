from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import MeetingGuest
from workspace.chat.services import calls
from workspace.chat.services.call_signaling import drain_events
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import create_meeting
from workspace.chat.services.participant_keys import guest_key, user_key
from workspace.chat.tests.meeting_fixtures import guest_with_token, make_event

User = get_user_model()


class MeetingUiBackendTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user("host", "host@example.com", "pw")
        self.event = make_event(
            self.host, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        self.meeting = create_meeting(self.event, self.host)
        self.occurrence_start = current_occurrence(self.meeting)[0]
        self.client.force_login(self.host)

    def test_call_state_carries_the_capacity(self):
        session, _participant, _created = calls.start_or_join_call(
            self.host, self.meeting.conversation_id
        )
        state = calls.serialize_call_state(session)
        self.assertEqual(state["max_participants"], calls.max_participants())

    def test_summary_carries_capacity_and_count(self):
        self.client.logout()
        resp = self.client.get(f"/api/v1/chat/meet/{self.meeting.slug}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["max_participants"], calls.max_participants())
        self.assertEqual(resp.json()["participant_count"], 0)

    def test_summary_participant_count_reflects_an_active_call(self):
        calls.start_or_join_call(self.host, self.meeting.conversation_id)
        self.client.logout()
        resp = self.client.get(f"/api/v1/chat/meet/{self.meeting.slug}")
        self.assertEqual(resp.json()["participant_count"], 1)

    def test_knock_returns_the_guests_own_participant_key_and_wakes_the_hosts(self):
        drain_events(user_key(self.host.id))
        self.client.logout()
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Visitor"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)
        guest = MeetingGuest.objects.get(meeting=self.meeting)
        self.assertEqual(resp.json()["participant_key"], guest_key(guest.uuid))
        events = drain_events(user_key(self.host.id))
        self.assertEqual([e["event"] for e in events], ["meeting_guest_waiting"])
        self.assertEqual(events[0]["data"]["display_name"], "Visitor")
        self.assertEqual(events[0]["data"]["guest_uuid"], str(guest.uuid))
        self.assertEqual(events[0]["data"]["meeting_id"], str(self.meeting.uuid))

    def test_join_and_state_return_the_guests_own_participant_key(self):
        guest, token = guest_with_token(
            self.meeting,
            self.occurrence_start,
            display_name="Visitor",
            state=MeetingGuest.State.ADMITTED,
        )
        calls.start_or_join_call(self.host, self.meeting.conversation_id)
        self.client.logout()
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/join",
            {"media_state": {"audio": True}},
            content_type="application/json",
            HTTP_X_MEETING_TOKEN=token,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["participant_key"], guest_key(guest.uuid))
        resp = self.client.get(
            f"/api/v1/chat/meet/{self.meeting.slug}/state", HTTP_X_MEETING_TOKEN=token
        )
        self.assertEqual(resp.json()["participant_key"], guest_key(guest.uuid))
        self.assertNotIn("conversation_id", resp.json())

    def test_state_returns_the_guests_own_participant_key_with_no_active_call(self):
        guest, token = guest_with_token(
            self.meeting,
            self.occurrence_start,
            display_name="Visitor",
            state=MeetingGuest.State.ADMITTED,
        )
        self.client.logout()
        resp = self.client.get(
            f"/api/v1/chat/meet/{self.meeting.slug}/state", HTTP_X_MEETING_TOKEN=token
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["admitted"], True)
        self.assertEqual(resp.json()["active"], False)
        self.assertEqual(resp.json()["participant_key"], guest_key(guest.uuid))
