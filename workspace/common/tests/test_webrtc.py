import os
from unittest import mock

from django.test import SimpleTestCase

from workspace.common.webrtc import DEFAULT_STUN_URL, build_ice_servers

_ENV_KEYS = (
    "CHAT_CALL_ICE_SERVERS",
    "CHAT_CALL_TURN_USERNAME",
    "CHAT_CALL_TURN_CREDENTIAL",
)


class BuildIceServersTests(SimpleTestCase):
    def _env(self, **overrides):
        # Preserve the ambient environment but force our keys to a known state
        # so the test is independent of the developer's shell.
        base = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
        base.update(overrides)
        return mock.patch.dict(os.environ, base, clear=True)

    def test_default_is_public_stun(self):
        with self._env():
            self.assertEqual(build_ice_servers(), [{"urls": DEFAULT_STUN_URL}])

    def test_comma_separated_urls(self):
        with self._env(CHAT_CALL_ICE_SERVERS="stun:a:1,stun:b:2"):
            self.assertEqual(
                build_ice_servers(),
                [{"urls": "stun:a:1"}, {"urls": "stun:b:2"}],
            )

    def test_turn_url_gets_credentials(self):
        with self._env(
            CHAT_CALL_ICE_SERVERS="turn:t:3478",
            CHAT_CALL_TURN_USERNAME="u",
            CHAT_CALL_TURN_CREDENTIAL="p",
        ):
            self.assertEqual(
                build_ice_servers(),
                [{"urls": "turn:t:3478", "username": "u", "credential": "p"}],
            )

    def test_turn_url_without_credentials_stays_bare(self):
        with self._env(CHAT_CALL_ICE_SERVERS="turn:t:3478"):
            self.assertEqual(build_ice_servers(), [{"urls": "turn:t:3478"}])

    def test_credentials_only_apply_to_turn_not_stun(self):
        with self._env(
            CHAT_CALL_ICE_SERVERS="stun:s:1,turn:t:3478",
            CHAT_CALL_TURN_USERNAME="u",
            CHAT_CALL_TURN_CREDENTIAL="p",
        ):
            self.assertEqual(
                build_ice_servers(),
                [
                    {"urls": "stun:s:1"},
                    {"urls": "turn:t:3478", "username": "u", "credential": "p"},
                ],
            )

    def test_blank_entries_are_skipped(self):
        with self._env(CHAT_CALL_ICE_SERVERS="stun:a:1, ,"):
            self.assertEqual(build_ice_servers(), [{"urls": "stun:a:1"}])
