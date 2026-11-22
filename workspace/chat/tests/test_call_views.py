from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
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

    def test_a_group_whose_bot_left_can_call(self):
        """A bot that left must not keep calls disabled for the people who
        remain."""
        from django.utils import timezone

        from workspace.ai.models import BotProfile

        User = get_user_model()
        bot = User.objects.create_user(username="leftbot", password="x")
        BotProfile.objects.create(user=bot, is_public=True)
        ConversationMember.objects.create(
            conversation=self.conv, user=bot, left_at=timezone.now()
        )

        self.client.force_authenticate(self.a)
        resp = self.client.post(self._url("/join"))
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(calls.get_active_call(self.conv.uuid))


@override_settings(
    CHAT_CALL_ICE_SERVERS=["stun:s:1", "turn:t:3478"],
    CHAT_CALL_TURN_USERNAME="static-user",
    CHAT_CALL_TURN_CREDENTIAL="static-credential",
    CHAT_CALL_TURN_SECRET="north",
    CHAT_CALL_TURN_TTL=3600,
)
class TurnCredentialTests(TestCase):
    """With a shared secret, TURN credentials are per user and only handed out
    by the call endpoints, never embedded in a page."""

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
        self.api = APIClient()

    def tearDown(self):
        cache.clear()

    def _turn(self, ice_servers):
        return next(s for s in ice_servers if s["urls"].startswith("turn:"))

    def _join(self, user):
        self.api.force_authenticate(user)
        resp = self.api.post(f"/api/v1/chat/conversations/{self.conv.uuid}/call/join")
        self.assertEqual(resp.status_code, 200)
        return self._turn(resp.data["ice_servers"])

    def test_join_hands_each_user_a_credential_of_their_own(self):
        turn_a, turn_b = self._join(self.a), self._join(self.b)
        self.assertTrue(turn_a["username"].endswith(f":{self.a.pk}"))
        self.assertTrue(turn_b["username"].endswith(f":{self.b.pk}"))
        self.assertNotEqual(turn_a["credential"], turn_b["credential"])
        self.assertNotEqual(turn_a["credential"], "static-credential")

    def test_join_credential_expires_within_the_ttl(self):
        frozen = timezone.now()
        with mock.patch("django.utils.timezone.now", return_value=frozen):
            turn = self._join(self.a)
        expires_at = int(turn["username"].split(":")[0])
        self.assertEqual(expires_at, int(frozen.timestamp()) + 3600)

    def test_diagnostic_endpoint_hands_out_a_derived_credential(self):
        self.api.force_authenticate(self.a)
        resp = self.api.get("/api/v1/chat/call/ice-servers")
        self.assertEqual(resp.status_code, 200)
        turn = self._turn(resp.data["ice_servers"])
        self.assertTrue(turn["username"].endswith(f":{self.a.pk}"))
        self.assertIn("no-store", resp["Cache-Control"])

    def test_diagnostic_endpoint_requires_a_session(self):
        resp = self.api.get("/api/v1/chat/call/ice-servers")
        self.assertIn(resp.status_code, (401, 403))

    def test_pages_embed_no_turn_credential(self):
        self.client.force_login(self.a)
        pages = [
            reverse("chat_ui:index"),
            reverse("chat_ui:room", kwargs={"conversation_uuid": self.conv.uuid}),
        ]
        for url in pages:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertNotIn("turn:t:3478", html)
                self.assertNotIn("static-credential", html)
