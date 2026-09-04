from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import (
    CallParticipant,
    CallSession,
    Conversation,
    ConversationMember,
    Meeting,
    MeetingGuest,
    Message,
)
from workspace.chat.services import call_signaling as sig
from workspace.chat.services import calls
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import end_meeting, set_locked
from workspace.chat.services.participant_keys import guest_key, user_key


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
        changed = calls.touch_presence(self.session_id, "u:1", {"audio": True})
        self.assertTrue(changed)
        self.assertEqual(calls.get_presence(self.session_id), {"u:1": {"audio": True}})

    def test_touch_same_state_reports_unchanged(self):
        calls.touch_presence(self.session_id, "u:1", {"audio": True})
        self.assertFalse(calls.touch_presence(self.session_id, "u:1", {"audio": True}))

    def test_touch_changed_state_reports_changed(self):
        calls.touch_presence(self.session_id, "u:1", {"audio": True})
        self.assertTrue(calls.touch_presence(self.session_id, "u:1", {"audio": False}))

    def test_member_and_guest_keys_do_not_collide(self):
        calls.touch_presence(self.session_id, "u:1", {"audio": True})
        calls.touch_presence(self.session_id, "g:1", {"audio": False})
        self.assertEqual(
            calls.get_presence(self.session_id),
            {"u:1": {"audio": True}, "g:1": {"audio": False}},
        )

    def test_drop_presence_removes_only_that_key(self):
        calls.touch_presence(self.session_id, "u:1", {"audio": True})
        calls.touch_presence(self.session_id, "u:2", {"audio": True})
        calls.drop_presence(self.session_id, "u:1")
        self.assertEqual(list(calls.get_presence(self.session_id)), ["u:2"])


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
        sig.drain_events(user_key(self.a.id))  # clear call_started
        calls.start_or_join_call(self.b, self.conv.uuid)
        envelopes = sig.drain_events(user_key(self.a.id))
        events = [e["event"] for e in envelopes]
        self.assertIn("call_participant_joined", events)
        joined = next(e for e in envelopes if e["event"] == "call_participant_joined")
        self.assertEqual(joined["data"]["participant_key"], user_key(self.b.id))
        self.assertEqual(joined["data"]["user_id"], self.b.id)
        self.assertEqual(joined["data"]["display_name"], self.b.username)

    def test_leave_broadcasts_participant_left_with_key(self):
        calls.start_or_join_call(self.a, self.conv.uuid)
        calls.start_or_join_call(self.b, self.conv.uuid)
        sig.drain_events(
            user_key(self.a.id)
        )  # clear call_started/call_participant_joined
        calls.leave_call(self.b, self.conv.uuid)
        envelopes = sig.drain_events(user_key(self.a.id))
        left = next(e for e in envelopes if e["event"] == "call_participant_left")
        self.assertEqual(left["data"]["participant_key"], user_key(self.b.id))

    def test_broadcast_exclude_key_skips_only_that_participant(self):
        calls.start_or_join_call(self.a, self.conv.uuid)
        calls.start_or_join_call(self.b, self.conv.uuid)
        sig.drain_events(user_key(self.a.id))
        sig.drain_events(user_key(self.b.id))
        calls._broadcast(
            self.conv.uuid,
            "call_participant_left",
            {"probe": True},
            exclude_key=user_key(self.a.id),
        )
        self.assertEqual(sig.drain_events(user_key(self.a.id)), [])
        events = [e["event"] for e in sig.drain_events(user_key(self.b.id))]
        self.assertIn("call_participant_left", events)

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


class BroadcastGuestTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.a = User.objects.create_user(username="bcg", password="x")
        cal = Calendar.objects.create(name="C", owner=self.a)
        event = Event.objects.create(
            calendar=cal, owner=self.a, title="E", start=timezone.now()
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.a
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.a)
        meeting = Meeting.objects.create(
            event=event, conversation=self.conv, created_by=self.a
        )
        self.guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name="Visitor",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=timezone.now(),
            token_hash="9" * 64,
        )

    def tearDown(self):
        cache.clear()

    def test_broadcast_reaches_an_admitted_guest_in_the_call(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        CallParticipant.objects.create(session=session, guest=self.guest)
        sig.drain_events(guest_key(self.guest.uuid))  # clear join noise
        calls._broadcast(
            self.conv.uuid,
            "call_participant_left",
            {"session_id": str(session.uuid)},
        )
        events = sig.drain_events(guest_key(self.guest.uuid))
        self.assertEqual([e["event"] for e in events], ["call_participant_left"])

    def test_broadcast_excludes_a_guest_by_key(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        CallParticipant.objects.create(session=session, guest=self.guest)
        key = guest_key(self.guest.uuid)
        sig.drain_events(key)
        calls._broadcast(self.conv.uuid, "x", {}, exclude_key=key)
        self.assertEqual(sig.drain_events(key), [])

    def test_broadcast_ignores_a_removed_guest(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        CallParticipant.objects.create(session=session, guest=self.guest)
        self.guest.state = MeetingGuest.State.REMOVED
        self.guest.save(update_fields=["state"])
        sig.drain_events(guest_key(self.guest.uuid))
        calls._broadcast(self.conv.uuid, "x", {})
        self.assertEqual(sig.drain_events(guest_key(self.guest.uuid)), [])

    def test_stale_sweep_still_reaches_the_guest_with_call_ended(self):
        # The sweep marks every stale row left_at BEFORE ending the call, and
        # _active_guest_keys only matches an ACTIVE, still-joined guest - so
        # _end_call's own recipient lookup found nobody and the guest never
        # learned the call was over.
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        CallParticipant.objects.create(session=session, guest=self.guest)
        key = guest_key(self.guest.uuid)
        sig.drain_events(key)
        cache.clear()

        self.assertTrue(calls.cleanup_stale_participants(session))

        events = [e["event"] for e in sig.drain_events(key)]
        self.assertIn("call_ended", events)

    def test_broadcast_ignores_a_departed_guest(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        p = CallParticipant.objects.create(session=session, guest=self.guest)
        p.left_at = timezone.now()
        p.save(update_fields=["left_at"])
        sig.drain_events(guest_key(self.guest.uuid))
        calls._broadcast(self.conv.uuid, "x", {})
        self.assertEqual(sig.drain_events(guest_key(self.guest.uuid)), [])


class StaleReconciliationTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.a = User.objects.create_user(username="sa", password="x")
        self.b = User.objects.create_user(username="sb", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.a
        )
        for u in (self.a, self.b):
            ConversationMember.objects.create(conversation=self.conv, user=u)

    def tearDown(self):
        cache.clear()

    def test_participant_without_a_heartbeat_is_reaped(self):
        calls.start_or_join_call(self.a, self.conv.uuid)
        session, _, _ = calls.start_or_join_call(self.b, self.conv.uuid)
        # Only a keeps a live heartbeat; b's expired.
        calls.drop_presence(session.uuid, f"u:{self.b.id}")
        calls.cleanup_stale_participants(session)
        remaining = {p.participant_key for p in calls.list_active_participants(session)}
        self.assertEqual(remaining, {f"u:{self.a.id}"})

    def test_call_ends_when_every_heartbeat_is_gone(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        calls.drop_presence(session.uuid, f"u:{self.a.id}")
        self.assertTrue(calls.cleanup_stale_participants(session))
        session.refresh_from_db()
        self.assertEqual(session.state, CallSession.State.ENDED)


class LockOutlivesTheCallTests(TestCase):
    """A durable lock lives until the host unlocks, presses End, or the
    occurrence it names stops being the current one. A call emptying out is
    none of those three: the room stays shut for the rest of the occurrence
    the host locked, which is what locking an occurrence means."""

    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.a = User.objects.create_user(username="lock-a", password="x")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.a
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.a)
        cal = Calendar.objects.create(name="C", owner=self.a)
        event = Event.objects.create(
            calendar=cal, owner=self.a, title="E", start=timezone.now()
        )
        self.meeting = Meeting.objects.create(
            event=event, conversation=self.conv, created_by=self.a
        )
        self.occurrence_start = current_occurrence(self.meeting)[0]

    def tearDown(self):
        cache.clear()

    def test_lock_survives_a_call_that_empties_out(self):
        calls.start_or_join_call(self.a, self.conv.uuid)
        set_locked(self.meeting, True)

        ended = calls.leave_call(self.a, self.conv.uuid)
        self.assertEqual(ended.state, CallSession.State.ENDED)

        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.locked_occurrence_start, self.occurrence_start)
        self.assertTrue(calls.is_call_locked(self.conv.uuid, self.occurrence_start))

    def test_lock_survives_the_stale_sweep(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        set_locked(self.meeting, True)

        calls.drop_presence(session.uuid, user_key(self.a.id))
        self.assertTrue(calls.cleanup_stale_participants(session))

        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.locked_occurrence_start, self.occurrence_start)
        self.assertTrue(calls.is_call_locked(self.conv.uuid, self.occurrence_start))

    def test_end_meeting_still_releases_the_lock(self):
        calls.start_or_join_call(self.a, self.conv.uuid)
        set_locked(self.meeting, True)

        self.assertTrue(end_meeting(self.meeting))

        self.meeting.refresh_from_db()
        self.assertIsNone(self.meeting.locked_occurrence_start)
        self.assertFalse(calls.is_call_locked(self.conv.uuid, self.occurrence_start))


class FirstJoinRaceTests(TestCase):
    """Two members starting a call in the same tiny window both pass the
    "no active call" check. The partial unique constraint lets only one
    CallSession be created; the loser must recover by joining the winner's
    freshly created session instead of surfacing the IntegrityError as a 500.
    """

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

    def test_loser_of_first_join_race_joins_winner_session(self):
        # a wins the race and commits the only active session.
        winner, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)

        # b is the loser: its first locked read still sees "no active call"
        # (the winner's row was created in a concurrent, not-yet-visible txn),
        # so it takes the create path and trips one_active_call_per_conversation.
        real_lookup = calls._active_session_for_update
        first_read = {"done": False}

        def stale_then_real(conversation_id):
            if not first_read["done"]:
                first_read["done"] = True
                return None
            return real_lookup(conversation_id)

        with mock.patch.object(
            calls, "_active_session_for_update", side_effect=stale_then_real
        ):
            session, participant, created = calls.start_or_join_call(
                self.b, self.conv.uuid
            )

        # Loser recovers into the winner's session, no error, not a new session.
        self.assertFalse(created)
        self.assertEqual(session.uuid, winner.uuid)
        self.assertIsNone(participant.left_at)
        self.assertEqual(participant.user_id, self.b.id)

        # Invariant intact: still exactly one active session, both members in it.
        self.assertEqual(
            CallSession.objects.filter(
                conversation=self.conv, state=CallSession.State.ACTIVE
            ).count(),
            1,
        )
        self.assertEqual(len(calls.list_active_participants(winner)), 2)

    def test_compound_race_retries_more_than_once_until_join(self):
        # A single retry only closes the two-party race. In a compound race the
        # second attempt can trip the constraint again (the winner ended its call
        # and a third member started a fresh one in the gap). As long as an active
        # session keeps existing, the recovery must keep retrying instead of
        # surfacing the second IntegrityError as a 500.
        from django.db import IntegrityError

        # A real active session so the race-winner guard passes on every attempt.
        winner, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        sentinel = (winner, object(), False)

        with mock.patch.object(
            calls,
            "_start_or_join_once",
            side_effect=[IntegrityError("x"), IntegrityError("y"), sentinel],
        ) as once:
            result = calls.start_or_join_call(self.b, self.conv.uuid)

        self.assertIs(result, sentinel)
        self.assertEqual(once.call_count, 3)

    def test_unrelated_integrity_error_propagates_without_retry(self):
        # The recovery is only for the first-join race, identified by an active
        # session now existing. An IntegrityError with no active session present
        # is some other failure and must propagate, not be retried/swallowed.
        from django.db import IntegrityError

        with mock.patch.object(
            calls, "_start_or_join_once", side_effect=IntegrityError("boom")
        ) as once:
            with self.assertRaises(IntegrityError):
                calls.start_or_join_call(self.b, self.conv.uuid)

        self.assertEqual(once.call_count, 1)


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
        self.assertEqual(state["started_at"], session.started_at.isoformat())
        self.assertEqual(len(state["participants"]), 1)
        self.assertEqual(state["participants"][0]["user_id"], self.a.id)
        self.assertEqual(state["participants"][0]["media_state"], {"audio": True})

    def test_media_state_video_and_screen_flags_roundtrip(self):
        # Contract: arbitrary media_state keys (video, screen) must survive
        # heartbeat -> presence cache -> serialize_call_state unchanged.
        # Pins the passthrough the frontend video feature depends on.
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        calls.touch_presence(
            session.uuid,
            f"u:{self.a.id}",
            {"audio": True, "video": True, "screen": False},
        )

        state = calls.serialize_call_state(session)
        me = next(p for p in state["participants"] if p["user_id"] == self.a.id)
        self.assertEqual(
            me["media_state"], {"audio": True, "video": True, "screen": False}
        )


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


class SerializeCallStateTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.a = User.objects.create_user(
            username="ser", password="x", first_name="Ada", last_name="L"
        )
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.a
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.a)

    def tearDown(self):
        cache.clear()

    def test_participant_carries_key_and_user_id(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        state = calls.serialize_call_state(session)
        self.assertEqual(len(state["participants"]), 1)
        p = state["participants"][0]
        self.assertEqual(p["participant_key"], f"u:{self.a.id}")
        self.assertEqual(p["user_id"], self.a.id)
        self.assertEqual(p["display_name"], "Ada L")
        self.assertEqual(p["media_state"], {"audio": True})

    def test_media_state_is_read_by_participant_key(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        calls.touch_presence(session.uuid, f"u:{self.a.id}", {"audio": False})
        state = calls.serialize_call_state(session)
        self.assertEqual(state["participants"][0]["media_state"], {"audio": False})

    def test_serialize_call_state_carries_locked_flag(self):
        session, _, _ = calls.start_or_join_call(self.a, self.conv.uuid)
        self.assertFalse(calls.serialize_call_state(session)["locked"])
        session.locked = True
        session.save(update_fields=["locked"])
        self.assertTrue(calls.serialize_call_state(session)["locked"])

    def test_guest_participant_serializes_with_display_name_and_no_user_id(self):
        cal = Calendar.objects.create(name="C", owner=self.a)
        event = Event.objects.create(
            calendar=cal, owner=self.a, title="E", start=timezone.now()
        )
        meeting = Meeting.objects.create(
            event=event, conversation=self.conv, created_by=self.a
        )
        guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name="Visitor",
            occurrence_start=timezone.now(),
            token_hash="9" * 64,
        )
        session = CallSession.objects.create(conversation=self.conv, started_by=self.a)
        CallParticipant.objects.create(session=session, guest=guest)

        state = calls.serialize_call_state(session)

        p = state["participants"][0]
        self.assertIsNone(p["user_id"])
        self.assertEqual(p["display_name"], "Visitor")
