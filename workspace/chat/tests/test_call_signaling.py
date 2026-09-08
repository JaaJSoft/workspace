from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.test import SimpleTestCase

from workspace.chat.services import call_signaling as sig


class CallSignalingTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_enqueue_then_drain_returns_event(self):
        sig.enqueue_event("u:1", "call_started", {"session_id": "s1"})
        out = sig.drain_events("u:1")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["event"], "call_started")
        self.assertEqual(out[0]["data"], {"session_id": "s1"})
        self.assertIn("id", out[0])

    def test_drain_clears_the_mailbox(self):
        sig.enqueue_event("u:1", "call_started", {})
        sig.drain_events("u:1")
        self.assertEqual(sig.drain_events("u:1"), [])

    def test_drain_is_isolated_per_participant(self):
        sig.enqueue_event("u:1", "a", {})
        sig.enqueue_event("u:2", "b", {})
        sig.enqueue_event("g:abc", "c", {})
        self.assertEqual([e["event"] for e in sig.drain_events("u:1")], ["a"])
        self.assertEqual([e["event"] for e in sig.drain_events("u:2")], ["b"])
        self.assertEqual([e["event"] for e in sig.drain_events("g:abc")], ["c"])

    def test_queue_is_capped(self):
        for i in range(sig.MAX_QUEUE + 50):
            sig.enqueue_event("u:1", "e", {"i": i})
        out = sig.drain_events("u:1")
        self.assertEqual(len(out), sig.MAX_QUEUE)
        # Oldest dropped: last item is the most recent enqueue.
        self.assertEqual(out[-1]["data"]["i"], sig.MAX_QUEUE + 49)

    def test_send_signal_enqueues_and_notifies_the_target(self):
        sess = uuid4()
        with patch("workspace.chat.services.call_signaling.notify_sse") as mock_notify:
            sig.send_signal(
                sess,
                to_participant="u:7",
                from_participant="u:3",
                signal={"type": "offer"},
            )
        mock_notify.assert_called_once_with("chat", 7)
        out = sig.drain_events("u:7")
        self.assertEqual(out[0]["event"], "call_signal")
        self.assertEqual(out[0]["data"]["from_participant"], "u:3")
        self.assertEqual(out[0]["data"]["signal"], {"type": "offer"})

    def test_notify_participant_wakes_a_member_stream(self):
        with patch("workspace.chat.services.call_signaling.notify_sse") as mock_notify:
            sig.notify_participant("u:9")
        mock_notify.assert_called_once_with("chat", 9)

    def test_notify_participant_is_a_noop_for_a_guest_key(self):
        # Guests do not drain the global stream. PR 3 gives them their own
        # transport; until then a guest key must not wake a member's stream.
        with patch("workspace.chat.services.call_signaling.notify_sse") as mock_notify:
            sig.notify_participant("g:abc")
        mock_notify.assert_not_called()

    def test_notify_participant_ignores_a_malformed_key(self):
        with patch("workspace.chat.services.call_signaling.notify_sse") as mock_notify:
            sig.notify_participant("garbage")
        mock_notify.assert_not_called()

    def test_send_diagnostic_signal_lands_in_the_sender_member_mailbox(self):
        with patch("workspace.chat.services.call_signaling.notify_sse") as mock_notify:
            sig.send_diagnostic_signal(5, "to_callee", {"type": "offer"}, "run-1")
        mock_notify.assert_called_once_with("chat", 5)
        out = sig.drain_events("u:5")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["event"], "call_diagnostic_signal")
        self.assertEqual(out[0]["data"]["lane"], "to_callee")
        self.assertEqual(out[0]["data"]["signal"], {"type": "offer"})
        self.assertEqual(out[0]["data"]["run_id"], "run-1")
