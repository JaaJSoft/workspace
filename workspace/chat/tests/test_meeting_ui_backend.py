from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import ConversationMember, MeetingGuest
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

    def test_the_summary_counts_members_and_guests_alike(self):
        session, _participant, _created = calls.start_or_join_call(
            self.host, self.meeting.conversation_id
        )
        guest, _token = guest_with_token(
            self.meeting, self.occurrence_start, state=MeetingGuest.State.ADMITTED
        )
        calls.join_call_as_guest(guest)

        self.assertEqual(calls.active_participant_count(session), 2)
        self.client.logout()
        resp = self.client.get(f"/api/v1/chat/meet/{self.meeting.slug}")
        self.assertEqual(resp.json()["participant_count"], 2)

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


class CallStartedReachesTheLobbyTests(TestCase):
    """An admitted guest waiting for the call to start is in no session yet,
    so the in-call fan-out cannot reach them. call_started is what tells them,
    and it is the only way they learn: nothing polls on their behalf."""

    def setUp(self):
        # The mailbox is the process-global LocMemCache, and a user id repeats
        # across TestCases: without this, another class's fan-out is what the
        # first drain here returns.
        cache.clear()
        self.host = User.objects.create_user("host", "host@example.com", "pw")
        self.event = make_event(
            self.host, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        self.meeting = create_meeting(self.event, self.host)
        self.occurrence_start = current_occurrence(self.meeting)[0]

    def tearDown(self):
        cache.clear()

    def _start_the_call(self):
        return calls.start_or_join_call(self.host, self.meeting.conversation_id)

    def test_an_admitted_guest_is_told_the_call_started(self):
        guest, _token = guest_with_token(
            self.meeting, self.occurrence_start, state=MeetingGuest.State.ADMITTED
        )
        self._start_the_call()

        events = drain_events(guest_key(guest.uuid))
        self.assertEqual([e["event"] for e in events], ["call_started"])

    def test_the_guests_copy_carries_no_conversation_id(self):
        guest, _token = guest_with_token(
            self.meeting, self.occurrence_start, state=MeetingGuest.State.ADMITTED
        )
        session, _participant, _created = self._start_the_call()

        payload = drain_events(guest_key(guest.uuid))[0]["data"]
        self.assertNotIn("conversation_id", payload)
        self.assertEqual(payload["session_id"], str(session.uuid))
        self.assertEqual(payload["started_by"], self.host.id)

    def test_the_member_still_receives_the_full_payload(self):
        guest_with_token(
            self.meeting, self.occurrence_start, state=MeetingGuest.State.ADMITTED
        )
        self._start_the_call()

        payload = drain_events(user_key(self.host.id))[0]["data"]
        self.assertEqual(payload["conversation_id"], str(self.meeting.conversation_id))

    def test_a_waiting_guest_is_told_nothing(self):
        guest, _token = guest_with_token(
            self.meeting, self.occurrence_start, state=MeetingGuest.State.WAITING
        )
        self._start_the_call()

        self.assertEqual(drain_events(guest_key(guest.uuid)), [])

    def test_a_guest_of_another_occurrence_is_told_nothing(self):
        guest, _token = guest_with_token(
            self.meeting,
            self.occurrence_start - timezone.timedelta(days=7),
            state=MeetingGuest.State.ADMITTED,
        )
        self._start_the_call()

        self.assertEqual(drain_events(guest_key(guest.uuid)), [])


class KnockFanOutTests(TestCase):
    """A knock wakes the hosts, and only the hosts: the mailbox is the one
    thing that puts a waiting guest in front of somebody."""

    def setUp(self):
        cache.clear()
        self.host = User.objects.create_user("host", "host@example.com", "pw")
        self.cohost = User.objects.create_user("cohost", "cohost@example.com", "pw")
        self.gone = User.objects.create_user("gone", "gone@example.com", "pw")
        self.event = make_event(
            self.host, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        self.meeting = create_meeting(self.event, self.host)
        ConversationMember.objects.create(
            conversation_id=self.meeting.conversation_id, user=self.cohost
        )
        ConversationMember.objects.create(
            conversation_id=self.meeting.conversation_id,
            user=self.gone,
            left_at=timezone.now(),
        )

    def tearDown(self):
        cache.clear()

    def test_knock_wakes_every_active_host_and_not_a_member_who_left(self):
        resp = self.client.post(
            f"/api/v1/chat/meet/{self.meeting.slug}/knock",
            {"display_name": "Visitor"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)

        for user in (self.host, self.cohost):
            events = drain_events(user_key(user.id))
            self.assertEqual(
                [e["event"] for e in events], ["meeting_guest_waiting"], user.username
            )
            self.assertEqual(events[0]["data"]["display_name"], "Visitor")
        self.assertEqual(drain_events(user_key(self.gone.id)), [])


class GuestLeaveFanOutTests(TestCase):
    """Leaving is only worth announcing when it closed something. A guest
    still on the waiting card - or leaving twice - holds no participant row,
    so there is no departure for anyone to render."""

    def setUp(self):
        cache.clear()
        self.host = User.objects.create_user("host", "host@example.com", "pw")
        self.event = make_event(
            self.host, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        self.meeting = create_meeting(self.event, self.host)
        self.occurrence_start = current_occurrence(self.meeting)[0]

    def tearDown(self):
        cache.clear()

    def test_leaving_with_no_participant_row_tells_the_host_nothing(self):
        guest, _token = guest_with_token(
            self.meeting, self.occurrence_start, state=MeetingGuest.State.ADMITTED
        )
        calls.start_or_join_call(self.host, self.meeting.conversation_id)
        drain_events(user_key(self.host.id))

        calls.leave_call_as_guest(guest)

        self.assertEqual(drain_events(user_key(self.host.id)), [])

    def test_leaving_an_actual_seat_still_tells_the_host(self):
        guest, _token = guest_with_token(
            self.meeting, self.occurrence_start, state=MeetingGuest.State.ADMITTED
        )
        calls.start_or_join_call(self.host, self.meeting.conversation_id)
        calls.join_call_as_guest(guest)
        drain_events(user_key(self.host.id))

        calls.leave_call_as_guest(guest)

        events = drain_events(user_key(self.host.id))
        self.assertEqual([e["event"] for e in events], ["call_participant_left"])
        self.assertEqual(events[0]["data"]["participant_key"], guest_key(guest.uuid))
