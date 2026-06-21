from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from workspace.chat.services.call_signaling import drain_events

User = get_user_model()


class CallDiagnosticSignalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")
        self.client = APIClient()
        self.url = reverse("chat-call-diagnostic-signal")

    def test_requires_authentication(self):
        resp = self.client.post(
            self.url, {"lane": "to_callee", "signal": {}, "run_id": "r1"}, format="json"
        )
        self.assertIn(resp.status_code, (401, 403))

    def test_valid_signal_echoes_to_sender_only(self):
        self.client.force_authenticate(self.user)
        signal = {"type": "offer", "sdp": "v=0..."}
        resp = self.client.post(
            self.url,
            {"lane": "to_callee", "signal": signal, "run_id": "run-123"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)

        # Echo lands in the sender's own mailbox...
        events = drain_events(self.user.id)
        self.assertEqual(len(events), 1)
        env = events[0]
        self.assertEqual(env["event"], "call_diagnostic_signal")
        self.assertEqual(env["data"]["lane"], "to_callee")
        self.assertEqual(env["data"]["signal"], signal)
        self.assertEqual(env["data"]["run_id"], "run-123")

        # ...and never leaks to another user.
        self.assertEqual(drain_events(self.other.id), [])

    def test_invalid_lane_rejected(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url,
            {"lane": "sideways", "signal": {}, "run_id": "r1"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(drain_events(self.user.id), [])

    def test_invalid_signal_type_rejected(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url,
            {"lane": "to_caller", "signal": "not-an-object", "run_id": "r1"},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_missing_run_id_rejected(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(
            self.url,
            {"lane": "to_caller", "signal": {}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()
