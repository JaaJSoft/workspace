from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import Conversation, ConversationMember, MeetingGuest
from workspace.chat.services import call_signaling as sig
from workspace.chat.services import calls
from workspace.chat.services.meeting_guests import issue_token
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import create_meeting
from workspace.chat.services.participant_keys import guest_key

User = get_user_model()


class CallViewTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.a = User.objects.create_user(username="a", password="x")
        self.b = User.objects.create_user(username="b", password="x")
        self.outsider = User.objects.create_user(username="out", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.a
        )
        for u in (self.a, self.b):
            ConversationMember.objects.create(conversation=self.conv, user=u)
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    def _url(self, suffix=""):
        return f"/api/v1/chat/conversations/{self.conv.uuid}/call{suffix}"

    def test_join_starts_call_and_returns_ice_servers(self):
        self.client.force_authenticate(self.a)
        resp = self.client.post(self._url("/join"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.data["state"]["active"])
        self.assertIn("ice_servers", resp.data)

    def test_outsider_cannot_join(self):
        self.client.force_authenticate(self.outsider)
        resp = self.client.post(self._url("/join"))
        self.assertEqual(resp.status_code, 404)

    def test_state_reports_inactive_then_active(self):
        self.client.force_authenticate(self.a)
        self.assertFalse(self.client.get(self._url()).data["active"])
        self.client.post(self._url("/join"))
        self.assertTrue(self.client.get(self._url()).data["active"])

    def test_full_room_returns_409(self):
        from django.test import override_settings

        self.client.force_authenticate(self.a)
        with override_settings(CHAT_CALL_MAX_PARTICIPANTS=1):
            self.client.post(self._url("/join"))
            self.client.force_authenticate(self.b)
            resp = self.client.post(self._url("/join"))
        self.assertEqual(resp.status_code, 409)

    def test_signal_is_delivered_to_target(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        self.client.force_authenticate(self.b)
        self.client.post(self._url("/join"))
        sig.drain_events(f"u:{self.b.id}")  # clear lifecycle noise
        self.client.force_authenticate(self.a)
        resp = self.client.post(
            self._url("/signal"),
            {
                "to_participant": f"u:{self.b.id}",
                "signal": {"type": "offer", "sdp": "x"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        delivered = [
            e for e in sig.drain_events(f"u:{self.b.id}") if e["event"] == "call_signal"
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["data"]["from_participant"], f"u:{self.a.id}")
        # The envelope must carry the active call SESSION id (not the
        # conversation id), so clients can scope signals to the right call.
        session = calls.get_active_call(self.conv.uuid)
        self.assertEqual(delivered[0]["data"]["session_id"], str(session.uuid))

    def test_signal_to_non_member_rejected(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        resp = self.client.post(
            self._url("/signal"),
            {"to_participant": f"u:{self.outsider.id}", "signal": {"type": "offer"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_heartbeat_updates_presence(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        resp = self.client.post(
            self._url("/heartbeat"),
            {"media_state": {"audio": False}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        session = calls.get_active_call(self.conv.uuid)
        self.assertEqual(
            calls.get_presence(session.uuid)[f"u:{self.a.id}"], {"audio": False}
        )

    def test_signal_rejects_a_boolean_participant(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        resp = self.client.post(
            self._url("/signal"),
            {"to_participant": True, "signal": {"type": "offer"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_leave_ends_solo_call(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        self.client.post(self._url("/leave"))
        self.assertIsNone(calls.get_active_call(self.conv.uuid))

    def test_cannot_start_call_in_bot_conversation(self):
        from workspace.ai.models import BotProfile

        User = get_user_model()
        bot = User.objects.create_user(username="callbot", password="x")
        BotProfile.objects.create(user=bot, is_public=True)
        bot_conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.a
        )
        ConversationMember.objects.create(conversation=bot_conv, user=self.a)
        ConversationMember.objects.create(conversation=bot_conv, user=bot)

        self.client.force_authenticate(self.a)
        resp = self.client.post(f"/api/v1/chat/conversations/{bot_conv.uuid}/call/join")
        self.assertEqual(resp.status_code, 400)
        self.assertIsNone(calls.get_active_call(bot_conv.uuid))

    def test_signal_relays_to_a_participant_key(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        resp = self.client.post(
            self._url("/signal"),
            {"to_participant": f"u:{self.b.id}", "signal": {"type": "offer"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        out = sig.drain_events(f"u:{self.b.id}")
        self.assertEqual(out[-1]["event"], "call_signal")
        self.assertEqual(out[-1]["data"]["from_participant"], f"u:{self.a.id}")

    def test_signal_rejects_a_missing_participant(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        resp = self.client.post(
            self._url("/signal"), {"signal": {"type": "offer"}}, format="json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_signal_rejects_a_malformed_key(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        resp = self.client.post(
            self._url("/signal"),
            {"to_participant": "garbage", "signal": {"type": "offer"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_signal_rejects_non_canonical_spellings_of_a_valid_target(self):
        # Each of these int()-parses to self.b.id, but none is the exact
        # spelling user_key() produces, so none may reach self.b's mailbox.
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        self.client.force_authenticate(self.b)
        self.client.post(self._url("/join"))
        sig.drain_events(f"u:{self.b.id}")  # clear lifecycle noise
        self.client.force_authenticate(self.a)
        for spelling in (
            f"u:0{self.b.id}",
            f"u: {self.b.id}",
            f"u:{self.b.id} ",
            f"u:+{self.b.id}",
            f"u:{self.b.id}\n",
            f"u:   {self.b.id}",
        ):
            resp = self.client.post(
                self._url("/signal"),
                {"to_participant": spelling, "signal": {"type": "offer"}},
                format="json",
            )
            self.assertEqual(resp.status_code, 400, spelling)
        self.assertEqual(sig.drain_events(f"u:{self.b.id}"), [])

    def test_signal_rejects_a_non_member_target(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        resp = self.client.post(
            self._url("/signal"),
            {
                "to_participant": f"u:{self.outsider.id}",
                "signal": {"type": "offer"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_heartbeat_broadcasts_the_participant_key_on_change(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        self.client.force_authenticate(self.b)
        self.client.post(self._url("/join"))
        sig.drain_events(f"u:{self.a.id}")
        self.client.post(
            self._url("/heartbeat"),
            {"media_state": {"audio": False}},
            format="json",
        )
        out = sig.drain_events(f"u:{self.a.id}")
        updates = [e for e in out if e["event"] == "call_participant_updated"]
        self.assertEqual(updates[-1]["data"]["participant_key"], f"u:{self.b.id}")

    def test_heartbeat_with_unchanged_media_state_uses_the_participant_key(self):
        # Repeats what join already recorded, so changed is False and the
        # broadcast branch - the only thing that would surface a wrong key - is
        # skipped.
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        session = calls.get_active_call(self.conv.uuid)
        sig.drain_events(f"u:{self.b.id}")  # clear the join broadcast
        resp = self.client.post(
            self._url("/heartbeat"),
            {"media_state": dict(calls.DEFAULT_MEDIA_STATE)},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        presence = calls.get_presence(session.uuid)
        self.assertIn(f"u:{self.a.id}", presence)
        self.assertNotIn(self.a.id, presence)
        self.assertNotIn(str(self.a.id), presence)
        # Proves changed was False: self.a's own heartbeat is excluded from
        # its own broadcast either way, so only a fellow member's mailbox can
        # show whether the update branch actually ran.
        updates = [
            e
            for e in sig.drain_events(f"u:{self.b.id}")
            if e["event"] == "call_participant_updated"
        ]
        self.assertEqual(updates, [])


def make_event(owner, start=None, end=None):
    cal = Calendar.objects.create(name="Cal", owner=owner)
    return Event.objects.create(
        calendar=cal,
        owner=owner,
        title="Standup",
        start=start or timezone.now(),
        end=end,
    )


class CallSignalToGuestTests(TestCase):
    """I3: a member must be able to signal an admitted meeting guest in the
    same call - without this, a guest's offer/ICE candidates have no route
    back and the handshake can never complete."""

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

    def _admit_and_join(self, meeting, display_name="Ada"):
        _token, token_hash = issue_token()
        guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name=display_name,
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=current_occurrence(meeting)[0],
            token_hash=token_hash,
        )
        calls.join_call_as_guest(guest)
        return guest

    def _url(self):
        return f"/api/v1/chat/conversations/{self.meeting.conversation_id}/call/signal"

    def test_member_can_signal_an_admitted_guest_in_the_same_call(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)
        guest = self._admit_and_join(self.meeting)
        sig.drain_events(guest_key(guest.uuid))  # clear lifecycle noise

        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self._url(),
            {"to_participant": guest_key(guest.uuid), "signal": {"type": "offer"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        delivered = [
            e
            for e in sig.drain_events(guest_key(guest.uuid))
            if e["event"] == "call_signal"
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["data"]["from_participant"], f"u:{self.owner.id}")

    def test_member_signal_to_guest_in_a_different_call_is_refused(self):
        calls.start_or_join_call(self.owner, self.meeting.conversation_id)

        other_owner = User.objects.create_user(username="other-host", password="x")
        other_event = make_event(other_owner)
        other_meeting = create_meeting(other_event, other_owner)
        calls.start_or_join_call(other_owner, other_meeting.conversation_id)
        other_guest = self._admit_and_join(other_meeting, display_name="Bea")

        self.client.force_authenticate(self.owner)
        resp = self.client.post(
            self._url(),
            {
                "to_participant": guest_key(other_guest.uuid),
                "signal": {"type": "offer"},
            },
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
