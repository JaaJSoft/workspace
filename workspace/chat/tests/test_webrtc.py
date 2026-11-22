from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase, override_settings

from workspace.chat.services.webrtc import ice_servers_for

NOW = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
ALICE = SimpleNamespace(pk=42)
BOB = SimpleNamespace(pk=43)


@override_settings(
    CHAT_CALL_ICE_SERVERS=["stun:s:1", "turn:t:3478", "turns:t:5349"],
    CHAT_CALL_TURN_USERNAME="",
    CHAT_CALL_TURN_CREDENTIAL="",
    CHAT_CALL_TURN_SECRET="",
    CHAT_CALL_TURN_TTL=3600,
)
class IceServersTests(SimpleTestCase):
    def test_without_credentials_every_url_stays_bare(self):
        self.assertEqual(
            ice_servers_for(ALICE),
            [{"urls": "stun:s:1"}, {"urls": "turn:t:3478"}, {"urls": "turns:t:5349"}],
        )

    @override_settings(CHAT_CALL_ICE_SERVERS=[])
    def test_no_url_means_no_server(self):
        self.assertEqual(ice_servers_for(ALICE), [])

    @override_settings(CHAT_CALL_TURN_USERNAME="u", CHAT_CALL_TURN_CREDENTIAL="p")
    def test_static_pair_goes_on_turn_urls_only(self):
        self.assertEqual(
            ice_servers_for(ALICE),
            [
                {"urls": "stun:s:1"},
                {"urls": "turn:t:3478", "username": "u", "credential": "p"},
                {"urls": "turns:t:5349", "username": "u", "credential": "p"},
            ],
        )


@override_settings(
    CHAT_CALL_ICE_SERVERS=["stun:s:1", "turn:t:3478"],
    CHAT_CALL_TURN_USERNAME="u",
    CHAT_CALL_TURN_CREDENTIAL="p",
    CHAT_CALL_TURN_SECRET="north",
    CHAT_CALL_TURN_TTL=3600,
)
@mock.patch("django.utils.timezone.now", return_value=NOW)
class SharedSecretTests(SimpleTestCase):
    def _turn(self, user):
        return next(s for s in ice_servers_for(user) if s["urls"].startswith("turn:"))

    def test_username_carries_the_expiry_and_the_user(self, _now):
        expires_at = int(NOW.timestamp()) + 3600
        self.assertEqual(self._turn(ALICE)["username"], f"{expires_at}:42")

    def test_credential_is_the_coturn_hmac_of_the_username(self, _now):
        # base64(HMAC-SHA1("north", "1795345200:42")), as coturn's use-auth-secret checks it.
        self.assertEqual(
            self._turn(ALICE),
            {
                "urls": "turn:t:3478",
                "username": "1795345200:42",
                "credential": "DV33IBD4u/TL0e9miz7UaXWkR6A=",
            },
        )

    def test_the_static_pair_is_never_handed_out(self, _now):
        turn = self._turn(ALICE)
        self.assertNotEqual(turn["username"], "u")
        self.assertNotEqual(turn["credential"], "p")

    def test_each_user_gets_their_own_credential(self, _now):
        self.assertNotEqual(self._turn(ALICE), self._turn(BOB))

    @override_settings(CHAT_CALL_TURN_TTL=7200)
    def test_lifetime_follows_the_setting(self, _now):
        expiry = int(self._turn(ALICE)["username"].split(":")[0])
        self.assertEqual(expiry - int(NOW.timestamp()), 7200)

    def test_stun_gets_no_credential(self, _now):
        self.assertEqual(ice_servers_for(ALICE)[0], {"urls": "stun:s:1"})
