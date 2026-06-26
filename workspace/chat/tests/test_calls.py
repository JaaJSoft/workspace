from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from workspace.chat.models import (
    CallParticipant,
    CallSession,
    Conversation,
    ConversationMember,
    Message,
)
from workspace.chat.services import call_signaling as sig
from workspace.chat.services import calls


class DurationFormatTests(SimpleTestCase):
    def test_seconds(self):
        self.assertEqual(calls.format_duration(0), "0s")
        self.assertEqual(calls.format_duration(45), "45s")

    def test_minutes(self):
        self.assertEqual(calls.format_duration(60), "1 min")
        self.assertEqual(calls.format_duration(12 * 60 + 5), "12 min")

    def test_hours(self):
        self.assertEqual(calls.format_duration(3600), "1 h 00")
        self.assertEqual(calls.format_duration(3665), "1 h 01")


@override_settings(CHAT_CALL_PRESENCE_TTL=12)
class PresenceTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.session_id = uuid4()

    def tearDown(self):
        cache.clear()

    def test_touch_then_get(self):
        changed = calls.touch_presence(self.session_id, 1, {"audio": True})
        self.assertTrue(changed)
        self.assertEqual(calls.get_presence(self.session_id), {"1": {"audio": True}})

    def test_touch_same_state_reports_unchanged(self):
        calls.touch_presence(self.session_id, 1, {"audio": True})
        self.assertFalse(calls.touch_presence(self.session_id, 1, {"audio": True}))

    def test_touch_changed_state_reports_changed(self):
        calls.touch_presence(self.session_id, 1, {"audio": True})
        self.assertTrue(calls.touch_presence(self.session_id, 1, {"audio": False}))


class LifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.a = User.objects.create_user(username="a", password="x")
        self.b = User.objects.create_user(username="b", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.a
        )
        for u in (self.a, self.b):
            ConversationMember.objects.create(conversation=self.conv, user=u)

    def tearDown(self):
        cache.clear()

    def test_start_creates_session_participant_and_system_message(self):
        session, participant, created = calls.start_or_join_call(self.a, self.conv.uuid)
        self.assertTrue(created)
        self.assertEqual(session.state, CallSession.State.ACTIVE)
        self.assertIsNone(participant.left_at)
        msg = session.system_message
        self.assertIsNotNone(msg)
        self.assertEqual(msg.kind, Message.Kind.SYSTEM)
        self.assertEqual(msg.tool_data["type"], "call")
        self.assertEqual(msg.tool_data["state"], "active")

    def test_second_caller_joins_same_session(self):
        s1, _, c1 = calls.start_or_join_call(self.a, self.conv.uuid)
        s2, _, c2 = calls.start_or_join_call(self.b, self.conv.uuid)
        self.assertTrue(c1)
        self.assertFalse(c2)
        self.assertEqual(s1.uuid, s2.uuid)
        self.assertEqual(len(calls.list_active_participants(s2)), 2)

    def test_join_broadcasts_participant_joined_to_members(self):
        calls.start_or_join_call(self.a, self.conv.uuid)
        sig.drain_events(self.a.id)  # clear call_started
        calls.start_or_join_call(self.b, self.conv.uuid)
        events = [e["event"] for e in sig.drain_events(self.a.id)]
        self.assertIn("call_participant_joined", events)

    def test_rejoin_reactivates_left_participant(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        calls.start_or_join_call(self.b, self.conv.uuid)
        calls.leave_call(self.b, self.conv.uuid)
        _, p, created = calls.start_or_join_call(self.b, self.conv.uuid)
        self.assertFalse(created)
        self.assertIsNone(p.left_at)
        self.assertEqual(
            CallParticipant.objects.filter(session=session, user=self.b).count(), 1
        )

    def test_last_leaver_ends_session_and_finalizes_message(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        ended = calls.leave_call(self.a, self.conv.uuid)
        self.assertEqual(ended.state, CallSession.State.ENDED)
        self.assertIsNotNone(ended.ended_at)
        ended.system_message.refresh_from_db()
        self.assertEqual(ended.system_message.tool_data["state"], "ended")
        self.assertIn("duration_label", ended.system_message.tool_data)
        self.assertIsNotNone(ended.system_message.edited_at)

    def test_full_room_raises(self):
        from django.test import override_settings

        with override_settings(CHAT_CALL_MAX_PARTICIPANTS=1):
            calls.start_or_join_call(self.a, self.conv.uuid)
            with self.assertRaises(calls.CallFull):
                calls.start_or_join_call(self.b, self.conv.uuid)


class CleanupTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.a = User.objects.create_user(username="a", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.a
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.a)

    def tearDown(self):
        cache.clear()

    def test_cleanup_ends_session_when_no_fresh_presence(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        cache.clear()  # wipe heartbeats -> everyone looks stale
        ended = calls.cleanup_stale_participants(session)
        self.assertTrue(ended)
        session.refresh_from_db()
        self.assertEqual(session.state, CallSession.State.ENDED)

    def test_cleanup_keeps_session_with_fresh_presence(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        ended = calls.cleanup_stale_participants(session)
        self.assertFalse(ended)
        session.refresh_from_db()
        self.assertEqual(session.state, CallSession.State.ACTIVE)

    def test_end_stale_calls_counts_ended(self):
        calls.start_or_join_call(self.a, self.conv.uuid)
        cache.clear()
        self.assertEqual(calls.end_stale_calls(), 1)

    def test_get_active_call_reaps_phantom_call_on_read(self):
        # A call whose heartbeats all expired (tab crash, lost network, server
        # or cache restart) leaves an ACTIVE row in the DB with no live
        # presence. The read path must self-heal so the banner stops advertising
        # a phantom call, without depending on the Celery beat sweep running.
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        cache.clear()  # wipe heartbeats: nobody is live anymore
        self.assertIsNone(calls.get_active_call(self.conv.uuid))
        session.refresh_from_db()
        self.assertEqual(session.state, CallSession.State.ENDED)

    def test_get_active_call_keeps_live_call(self):
        # A call with a fresh heartbeat must survive the self-heal read path.
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        self.assertIsNotNone(calls.get_active_call(self.conv.uuid))
        session.refresh_from_db()
        self.assertEqual(session.state, CallSession.State.ACTIVE)

    def test_serialize_call_state(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        state = calls.serialize_call_state(session)
        self.assertTrue(state["active"])
        self.assertEqual(state["session_id"], str(session.uuid))
        self.assertIn("started_at", state)
        self.assertTrue(state["started_at"])  # non-empty ISO string
        self.assertEqual(len(state["participants"]), 1)
        self.assertEqual(state["participants"][0]["user_id"], self.a.id)
        self.assertEqual(state["participants"][0]["media_state"], {"audio": True})


class EndStaleCallsTaskTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.a = User.objects.create_user(username="a", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.a
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.a)

    def tearDown(self):
        cache.clear()

    def test_task_ends_stale_call(self):
        from workspace.chat.tasks import end_stale_calls

        calls.start_or_join_call(self.a, self.conv.uuid)
        cache.clear()
        self.assertEqual(end_stale_calls(), 1)
        self.assertIsNone(calls.get_active_call(self.conv.uuid))
