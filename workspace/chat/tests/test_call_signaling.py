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
        sig.enqueue_event(1, "call_started", {"session_id": "s1"})
        out = sig.drain_events(1)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["event"], "call_started")
        self.assertEqual(out[0]["data"], {"session_id": "s1"})
        self.assertIn("id", out[0])

    def test_drain_clears_the_mailbox(self):
        sig.enqueue_event(1, "call_started", {})
        sig.drain_events(1)
        self.assertEqual(sig.drain_events(1), [])

    def test_drain_is_isolated_per_user(self):
        sig.enqueue_event(1, "a", {})
        sig.enqueue_event(2, "b", {})
        self.assertEqual([e["event"] for e in sig.drain_events(1)], ["a"])
        self.assertEqual([e["event"] for e in sig.drain_events(2)], ["b"])

    def test_queue_is_capped(self):
        for i in range(sig.MAX_QUEUE + 50):
            sig.enqueue_event(1, "e", {"i": i})
        out = sig.drain_events(1)
        self.assertEqual(len(out), sig.MAX_QUEUE)
        # Oldest dropped: last item is the most recent enqueue.
        self.assertEqual(out[-1]["data"]["i"], sig.MAX_QUEUE + 49)

    def test_send_signal_enqueues_call_signal_and_notifies(self):
        sess = uuid4()
        with self.settings():
            sig.send_signal(
                sess, to_user_id=7, from_user_id=3, signal={"type": "offer"}
            )
        out = sig.drain_events(7)
        self.assertEqual(out[0]["event"], "call_signal")
        self.assertEqual(out[0]["data"]["from_user_id"], 3)
        self.assertEqual(out[0]["data"]["signal"], {"type": "offer"})
        self.assertEqual(out[0]["data"]["session_id"], str(sess))
