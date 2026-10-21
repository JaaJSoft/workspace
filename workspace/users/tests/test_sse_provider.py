"""Tests for workspace.users.sse_provider.

What a presence stream carries (the viewer's watched users only) and how much
it sends once connected (nothing until a status it follows changes).
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.users.services import presence_stream
from workspace.users.sse_provider import POLL_INTERVAL, PresenceSSEProvider
from workspace.users.tests.test_presence_stream import PresenceStreamResetMixin

User = get_user_model()


class PresenceSSEProviderTests(PresenceStreamResetMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="caller", password="pass")

    def setUp(self):
        super().setUp()
        self.instance = {}
        patcher = mock.patch.object(
            presence_stream, "instance_statuses", side_effect=lambda: self.instance
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _connect(self):
        provider = PresenceSSEProvider(user=self.user, last_event_id=None)
        events = provider.get_initial_events()
        return provider, events

    @staticmethod
    def _elapse(provider):
        provider._last_check -= POLL_INTERVAL + 1

    def test_snapshot_carries_only_watched_users(self):
        presence_stream.watch(self.user.id, [101, 104])
        self.instance = {
            101: "online",
            102: "online",
            103: "busy",
            104: "bot",
            105: "bot",
            self.user.id: "away",
        }

        _, events = self._connect()

        self.assertEqual(
            events,
            [
                (
                    "presence_snapshot",
                    {"online": [101], "away": [self.user.id], "busy": [], "bot": [104]},
                    None,
                )
            ],
        )

    def test_unchanged_statuses_send_nothing_after_the_snapshot(self):
        presence_stream.watch(self.user.id, [101, 102])
        self.instance = {101: "online"}
        provider, _ = self._connect()

        self._elapse(provider)
        self.assertEqual(provider.poll(cache_value=None), [])

    def test_changes_of_unwatched_users_send_nothing(self):
        presence_stream.watch(self.user.id, [101])
        self.instance = {101: "online"}
        provider, _ = self._connect()

        self.instance = {101: "online", 102: "online", 103: "busy"}
        self._elapse(provider)
        self.assertEqual(provider.poll(cache_value=None), [])

    def test_poll_sends_only_what_changed(self):
        presence_stream.watch(self.user.id, [101, 102, 103])
        self.instance = {101: "online", 102: "online"}
        provider, _ = self._connect()

        self.instance = {101: "online", 102: "away", 103: "busy"}
        self._elapse(provider)
        self.assertEqual(
            provider.poll(cache_value=None),
            [("presence_changes", {"102": "away", "103": "busy"}, None)],
        )

        # A user going offline is a change too, sent explicitly.
        self.instance = {102: "away", 103: "busy"}
        self._elapse(provider)
        self.assertEqual(
            provider.poll(cache_value=None),
            [("presence_changes", {"101": "offline"}, None)],
        )

    def test_poll_waits_for_the_interval(self):
        presence_stream.watch(self.user.id, [101])
        provider, _ = self._connect()

        self.instance = {101: "online"}
        self.assertEqual(provider.poll(cache_value=None), [])

    def test_a_watch_notification_answers_for_the_new_ids_at_once(self):
        presence_stream.watch(self.user.id, [101])
        self.instance = {101: "online", 102: "busy"}
        provider, _ = self._connect()

        presence_stream.watch(self.user.id, [102, 103])
        # Notified, so the interval does not hold the answer back.
        self.assertEqual(
            provider.poll(cache_value="notified"),
            [("presence_changes", {"102": "busy", "103": "offline"}, None)],
        )
