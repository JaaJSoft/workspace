"""Tests for workspace.users.sse_provider.

Exercises the real DB-to-snapshot bucketing logic and the
PresenceSSEProvider poll/initial-event flow. The process-level
snapshot cache is reset between tests to avoid cross-test bleed.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.users import sse_provider
from workspace.users.models import UserPresence
from workspace.users.sse_provider import (
    PresenceSSEProvider,
    _build_global_snapshot,
    _query_presence_snapshot,
)

User = get_user_model()


class _SnapshotResetMixin:
    def setUp(self):
        # Process-level cache — reset so each test starts fresh.
        sse_provider._cached_snapshot = None
        sse_provider._cached_snapshot_ts = 0
        cache.delete('presence:bot_user_ids')


class QueryPresenceSnapshotTests(_SnapshotResetMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()

        def _presence(username, *, delta, manual='auto'):
            user = User.objects.create_user(username=username, password='pass')
            UserPresence.objects.create(
                user=user,
                last_seen=now - delta,
                last_activity=now - delta,
                manual_status=manual,
            )
            return user

        cls.now = now
        cls.online_user = _presence('online', delta=timedelta(seconds=30))
        cls.away_by_age = _presence('away_age', delta=timedelta(minutes=5))
        cls.offline_user = _presence('offline', delta=timedelta(minutes=30))
        cls.busy_user = _presence('busy', delta=timedelta(minutes=30), manual='busy')
        cls.manual_away = _presence('manual_away', delta=timedelta(minutes=30), manual='away')
        cls.invisible_user = _presence(
            'invisible', delta=timedelta(seconds=10), manual='invisible',
        )

    def test_buckets_users_by_status(self):
        with mock.patch.object(
            PresenceSSEProvider, '_get_bot_ids', return_value=[]
        ):
            snapshot = _query_presence_snapshot()

        self.assertIn(self.online_user.id, snapshot['online'])
        self.assertNotIn(self.online_user.id, snapshot['away'])

        self.assertIn(self.away_by_age.id, snapshot['away'])
        self.assertIn(self.manual_away.id, snapshot['away'])

        self.assertIn(self.busy_user.id, snapshot['busy'])

        # Invisible users never surface even though they were recently active.
        for key in ('online', 'away', 'busy'):
            self.assertNotIn(self.invisible_user.id, snapshot[key])

        # Offline (auto, stale) users are omitted.
        for key in ('online', 'away', 'busy'):
            self.assertNotIn(self.offline_user.id, snapshot[key])

    def test_bot_ids_propagated(self):
        with mock.patch.object(
            PresenceSSEProvider, '_get_bot_ids', return_value=[42, 43],
        ):
            snapshot = _query_presence_snapshot()
        self.assertEqual(snapshot['bot'], [42, 43])


class BuildGlobalSnapshotTests(_SnapshotResetMixin, TestCase):
    def test_cache_hit_reuses_previous_snapshot(self):
        with mock.patch(
            'workspace.users.sse_provider._query_presence_snapshot',
            return_value={'online': [1], 'away': [], 'busy': [], 'bot': []},
        ) as query:
            first = _build_global_snapshot()
            second = _build_global_snapshot()

        # Second call must hit the cache, not re-query.
        query.assert_called_once()
        self.assertIs(first, second)

    def test_cache_miss_triggers_requery(self):
        with mock.patch(
            'workspace.users.sse_provider._query_presence_snapshot',
            return_value={'online': [1], 'away': [], 'busy': [], 'bot': []},
        ) as query:
            _build_global_snapshot()

        # Expire the cache manually.
        sse_provider._cached_snapshot_ts = 0

        with mock.patch(
            'workspace.users.sse_provider._query_presence_snapshot',
            return_value={'online': [1, 2], 'away': [], 'busy': [], 'bot': []},
        ) as query2:
            snapshot = _build_global_snapshot()

        query.assert_called_once()
        query2.assert_called_once()
        self.assertEqual(snapshot['online'], [1, 2])


class PresenceSSEProviderTests(_SnapshotResetMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username='caller', password='pass')

    def _make_provider(self):
        return PresenceSSEProvider(user=self.user, last_event_id=None)

    def test_initial_events_returns_snapshot(self):
        fake_snapshot = {'online': [1], 'away': [], 'busy': [], 'bot': []}
        with mock.patch(
            'workspace.users.sse_provider._build_global_snapshot',
            return_value=fake_snapshot,
        ):
            events = self._make_provider().get_initial_events()

        self.assertEqual(len(events), 1)
        event_name, payload, event_id = events[0]
        self.assertEqual(event_name, 'presence_snapshot')
        self.assertEqual(payload, fake_snapshot)
        self.assertIsNone(event_id)

    def test_poll_suppresses_updates_before_interval(self):
        with mock.patch(
            'workspace.users.sse_provider._build_global_snapshot',
            return_value={'online': [], 'away': [], 'busy': [], 'bot': []},
        ):
            provider = self._make_provider()
            provider.get_initial_events()
            # Immediately poll — should be throttled (<10 s since initial).
            events = provider.poll(cache_value=None)
        self.assertEqual(events, [])

    def test_poll_emits_when_snapshot_changes(self):
        initial = {'online': [1], 'away': [], 'busy': [], 'bot': []}
        updated = {'online': [1, 2], 'away': [], 'busy': [], 'bot': []}

        with mock.patch(
            'workspace.users.sse_provider._build_global_snapshot',
            return_value=initial,
        ):
            provider = self._make_provider()
            provider.get_initial_events()

        # Pretend 11 seconds have passed so poll() is allowed to fire.
        provider._last_push -= 11

        with mock.patch(
            'workspace.users.sse_provider._build_global_snapshot',
            return_value=updated,
        ):
            events = provider.poll(cache_value=None)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], updated)

    def test_poll_suppresses_when_snapshot_unchanged(self):
        snapshot = {'online': [1], 'away': [], 'busy': [], 'bot': []}
        with mock.patch(
            'workspace.users.sse_provider._build_global_snapshot',
            return_value=snapshot,
        ):
            provider = self._make_provider()
            provider.get_initial_events()
            provider._last_push -= 11
            events = provider.poll(cache_value=None)

        self.assertEqual(events, [])


class BotIdsCacheTests(_SnapshotResetMixin, TestCase):
    def test_bot_ids_are_cached(self):
        from workspace.ai.models import BotProfile
        bot_user = User.objects.create_user(username='bot1', password='pass')
        BotProfile.objects.create(user=bot_user)

        ids = PresenceSSEProvider._get_bot_ids()
        self.assertEqual(ids, [bot_user.id])

        # Delete the profile; cached result must be returned on the next call.
        BotProfile.objects.filter(user=bot_user).delete()
        ids_after = PresenceSSEProvider._get_bot_ids()
        self.assertEqual(ids_after, [bot_user.id])
