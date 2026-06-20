from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services import call_signaling as sig
from workspace.chat.services import calls


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
        sig.drain_events(self.b.id)  # clear lifecycle noise
        self.client.force_authenticate(self.a)
        resp = self.client.post(
            self._url("/signal"),
            {"to_user_id": self.b.id, "signal": {"type": "offer", "sdp": "x"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        delivered = [
            e for e in sig.drain_events(self.b.id) if e["event"] == "call_signal"
        ]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["data"]["from_user_id"], self.a.id)
        # The envelope must carry the active call SESSION id (not the
        # conversation id), so clients can scope signals to the right call.
        session = calls.get_active_call(self.conv.uuid)
        self.assertEqual(delivered[0]["data"]["session_id"], str(session.uuid))

    def test_signal_to_non_member_rejected(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        resp = self.client.post(
            self._url("/signal"),
            {"to_user_id": self.outsider.id, "signal": {"type": "offer"}},
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
            calls.get_presence(session.uuid)[str(self.a.id)], {"audio": False}
        )

    def test_signal_with_boolean_user_id_rejected(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        resp = self.client.post(
            self._url("/signal"),
            {"to_user_id": True, "signal": {"type": "offer"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_leave_ends_solo_call(self):
        self.client.force_authenticate(self.a)
        self.client.post(self._url("/join"))
        self.client.post(self._url("/leave"))
        self.assertIsNone(calls.get_active_call(self.conv.uuid))
