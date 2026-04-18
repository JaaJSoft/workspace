from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.core.activity_registry import (
    ActivityProvider,
    ActivityProviderInfo,
    ActivityRegistry,
)

User = get_user_model()


class StubProvider(ActivityProvider):
    """Minimal concrete provider for testing."""

    def __init__(self):
        self._daily_counts = {}
        self._events = []
        self._stats = {}

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        return self._daily_counts

    def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
        return self._events[offset:offset + limit]

    def get_stats(self, user_id, *, viewer_id=None):
        return self._stats


class ActivityRegistryTests(TestCase):

    def setUp(self):
        self.registry = ActivityRegistry()

    def test_register_provider(self):
        info = ActivityProviderInfo(
            slug='files', label='Files', icon='hard-drive', color='primary',
            provider_cls=StubProvider,
        )
        self.registry.register(info)
        self.assertIn('files', self.registry.get_all())

    def test_register_duplicate_slug_raises(self):
        info = ActivityProviderInfo(
            slug='files', label='Files', icon='hard-drive', color='primary',
            provider_cls=StubProvider,
        )
        self.registry.register(info)
        with self.assertRaises(ValueError):
            self.registry.register(info)

    def test_get_provider_returns_instance(self):
        info = ActivityProviderInfo(
            slug='files', label='Files', icon='hard-drive', color='primary',
            provider_cls=StubProvider,
        )
        self.registry.register(info)
        provider = self.registry.get_provider('files')
        self.assertIsInstance(provider, StubProvider)

    def test_get_provider_unknown_returns_none(self):
        self.assertIsNone(self.registry.get_provider('unknown'))

    def test_get_daily_counts_aggregates(self):
        class ProviderA(StubProvider):
            def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
                return {date(2026, 3, 1): 2, date(2026, 3, 2): 1}

        class ProviderB(StubProvider):
            def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
                return {date(2026, 3, 1): 3}

        self.registry.register(ActivityProviderInfo(
            slug='a', label='A', icon='a', color='primary', provider_cls=ProviderA,
        ))
        self.registry.register(ActivityProviderInfo(
            slug='b', label='B', icon='b', color='info', provider_cls=ProviderB,
        ))
        counts = self.registry.get_daily_counts(1, date(2026, 3, 1), date(2026, 3, 2))
        self.assertEqual(counts[date(2026, 3, 1)], 5)
        self.assertEqual(counts[date(2026, 3, 2)], 1)

    def test_get_recent_events_merges_and_sorts(self):
        from datetime import datetime
        from django.utils import timezone

        ts1 = timezone.make_aware(datetime(2026, 3, 1, 10, 0))
        ts2 = timezone.make_aware(datetime(2026, 3, 1, 12, 0))
        ts3 = timezone.make_aware(datetime(2026, 3, 1, 11, 0))

        class ProviderA(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                return [
                    {'label': 'e1', 'timestamp': ts1},
                    {'label': 'e2', 'timestamp': ts2},
                ]

        class ProviderB(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                return [{'label': 'e3', 'timestamp': ts3}]

        self.registry.register(ActivityProviderInfo(
            slug='a', label='A', icon='a', color='primary', provider_cls=ProviderA,
        ))
        self.registry.register(ActivityProviderInfo(
            slug='b', label='B', icon='b', color='info', provider_cls=ProviderB,
        ))
        events = self.registry.get_recent_events(1, limit=3)
        labels = [e['label'] for e in events]
        self.assertEqual(labels, ['e2', 'e3', 'e1'])

    def test_get_recent_events_with_source_filter(self):
        from datetime import datetime
        from django.utils import timezone

        ts = timezone.make_aware(datetime(2026, 3, 1, 10, 0))

        class ProviderA(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                return [{'label': 'from_a', 'timestamp': ts}]

        class ProviderB(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                return [{'label': 'from_b', 'timestamp': ts}]

        self.registry.register(ActivityProviderInfo(
            slug='a', label='A', icon='a', color='primary', provider_cls=ProviderA,
        ))
        self.registry.register(ActivityProviderInfo(
            slug='b', label='B', icon='b', color='info', provider_cls=ProviderB,
        ))
        events = self.registry.get_recent_events(1, source='a')
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['label'], 'from_a')

    def test_get_recent_events_offset_and_limit(self):
        from datetime import datetime, timedelta
        from django.utils import timezone

        base = timezone.make_aware(datetime(2026, 3, 1, 10, 0))

        class BigProvider(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                return [
                    {'label': f'e{i}', 'timestamp': base + timedelta(hours=i)}
                    for i in range(5)
                ]

        self.registry.register(ActivityProviderInfo(
            slug='big', label='Big', icon='b', color='info', provider_cls=BigProvider,
        ))
        events = self.registry.get_recent_events(1, limit=2, offset=1)
        self.assertEqual(len(events), 2)
        # Sorted desc: e4, e3, e2, e1, e0 -> offset=1 -> e3, e2
        self.assertEqual(events[0]['label'], 'e3')
        self.assertEqual(events[1]['label'], 'e2')

    def test_get_stats_aggregates(self):
        class ProviderA(StubProvider):
            def get_stats(self, user_id, *, viewer_id=None):
                return {'total_files': 42}

        class ProviderB(StubProvider):
            def get_stats(self, user_id, *, viewer_id=None):
                return {'total_messages': 100}

        self.registry.register(ActivityProviderInfo(
            slug='a', label='A', icon='a', color='primary', provider_cls=ProviderA,
        ))
        self.registry.register(ActivityProviderInfo(
            slug='b', label='B', icon='b', color='info', provider_cls=ProviderB,
        ))
        stats = self.registry.get_stats(1)
        self.assertEqual(stats, {'a': {'total_files': 42}, 'b': {'total_messages': 100}})

    def test_get_daily_counts_empty_registry(self):
        counts = self.registry.get_daily_counts(1, date(2026, 3, 1), date(2026, 3, 2))
        self.assertEqual(counts, {})

    def test_get_recent_events_empty_registry(self):
        events = self.registry.get_recent_events(1)
        self.assertEqual(events, [])

    def test_get_stats_empty_registry(self):
        stats = self.registry.get_stats(1)
        self.assertEqual(stats, {})

    def test_get_recent_events_exclude_actor_in_all_mode(self):
        """Events from the excluded actor must not crowd out other actors.

        Regression test: when source=None (ALL), the registry merges events
        from every provider, sorts by timestamp, and slices.  If the excluded
        actor has many recent events across providers, they used to push other
        actors' events out of the window *before* the exclude filter ran,
        resulting in an empty feed even though other actors had activity.
        """
        from datetime import datetime, timedelta
        from django.utils import timezone

        now = timezone.make_aware(datetime(2026, 3, 15, 12, 0))

        class DominantProvider(StubProvider):
            """Returns many events from actor 1 (the excluded user)."""
            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                return [
                    {'label': f'dominant-{i}', 'timestamp': now - timedelta(minutes=i),
                     'actor': {'id': 1, 'username': 'excluded'}}
                    for i in range(limit)
                ]

        class MinorProvider(StubProvider):
            """Returns a single older event from actor 2 (another user)."""
            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                return [
                    {'label': 'minor-event', 'timestamp': now - timedelta(hours=2),
                     'actor': {'id': 2, 'username': 'other'}},
                ]

        self.registry.register(ActivityProviderInfo(
            slug='dominant', label='D', icon='d', color='primary',
            provider_cls=DominantProvider,
        ))
        self.registry.register(ActivityProviderInfo(
            slug='minor', label='M', icon='m', color='info',
            provider_cls=MinorProvider,
        ))

        # Without exclude: ALL returns dominant events first
        events = self.registry.get_recent_events(None, limit=5)
        self.assertTrue(all(e['actor']['id'] == 1 for e in events))

        # With exclude_actor_id: the minor provider's event must survive
        events = self.registry.get_recent_events(
            None, limit=5, exclude_actor_id=1,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['label'], 'minor-event')
        self.assertEqual(events[0]['actor']['id'], 2)

    def test_exclude_actor_id_passes_through_null_actor_events(self):
        """Events with actor=None must not be excluded when exclude_actor_id is set.

        Regression test for activity_registry.py: the filter previously
        used e.get('actor', {}).get('id') which crashed on actor=None
        (because None.get(...) raises AttributeError, not because the
        default {} was used). The fix is (e.get('actor') or {}).get('id').
        """
        from datetime import datetime, timedelta
        from django.utils import timezone

        now = timezone.make_aware(datetime(2026, 3, 15, 12, 0))

        class MixedActorProvider(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                return [
                    {'label': 'null-actor-event', 'timestamp': now - timedelta(minutes=1),
                     'actor': None},
                    {'label': 'excluded-actor-event', 'timestamp': now - timedelta(minutes=2),
                     'actor': {'id': 2, 'username': 'excluded'}},
                ]

        self.registry.register(ActivityProviderInfo(
            slug='mixed_actor', label='MA', icon='m', color='info',
            provider_cls=MixedActorProvider,
        ))

        events = self.registry.get_recent_events(None, limit=10, exclude_actor_id=2)
        labels = [e['label'] for e in events]
        self.assertIn('null-actor-event', labels)
        self.assertNotIn('excluded-actor-event', labels)

    def test_exclude_actor_not_applied_to_single_source(self):
        """exclude_actor_id in the registry only filters in ALL mode.

        For single-source queries, the service layer handles exclusion
        with its own over-fetch buffer.
        """
        from datetime import datetime
        from django.utils import timezone

        ts = timezone.make_aware(datetime(2026, 3, 15, 12, 0))

        class MixedProvider(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                return [
                    {'label': 'own', 'timestamp': ts, 'actor': {'id': 1}},
                    {'label': 'other', 'timestamp': ts, 'actor': {'id': 2}},
                ]

        self.registry.register(ActivityProviderInfo(
            slug='mixed', label='M', icon='m', color='info',
            provider_cls=MixedProvider,
        ))

        # Single-source: exclude_actor_id is NOT applied at registry level
        events = self.registry.get_recent_events(
            None, limit=10, source='mixed', exclude_actor_id=1,
        )
        self.assertEqual(len(events), 2)

    def test_provider_exception_is_handled_gracefully(self):
        class FailingProvider(ActivityProvider):
            def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
                raise RuntimeError("boom")

            def get_recent_events(self, user_id, limit=10, offset=0, *, viewer_id=None):
                raise RuntimeError("boom")

            def get_stats(self, user_id, *, viewer_id=None):
                raise RuntimeError("boom")

        self.registry.register(ActivityProviderInfo(
            slug='fail', label='Fail', icon='x', color='error', provider_cls=FailingProvider,
        ))
        counts = self.registry.get_daily_counts(1, date(2026, 3, 1), date(2026, 3, 2))
        self.assertEqual(counts, {})
        events = self.registry.get_recent_events(1)
        self.assertEqual(events, [])
        stats = self.registry.get_stats(1)
        self.assertEqual(stats, {'fail': {}})


class ActivityServiceTests(TestCase):
    """Tests for workspace.core.services.activity."""

    def test_exclude_user_id_passes_through_null_actor_events(self):
        """Events with actor=None must not crash the exclude filter.

        Regression test: activity_service.get_recent_events did
        e.get('actor', {}).get('id'), which crashes on actor=None
        (dict.get returns the stored None, not the default). The
        calendar provider emits actor=None for external-feed events,
        and the dashboard view calls the service with exclude_user_id
        set, so a user with any external calendar event hit a 500 on /.
        """
        from unittest.mock import patch

        from workspace.core.services import activity as activity_service

        events_from_registry = [
            {'label': 'null-actor', 'actor': None, 'timestamp': None},
            {'label': 'other-actor', 'actor': {'id': 42}, 'timestamp': None},
            {'label': 'excluded', 'actor': {'id': 7}, 'timestamp': None},
        ]

        with patch.object(
            activity_service.activity_registry,
            'get_recent_events',
            return_value=list(events_from_registry),
        ):
            result = activity_service.get_recent_events(
                viewer_id=7, exclude_user_id=7, limit=10,
            )

        labels = [e['label'] for e in result]
        self.assertIn('null-actor', labels)
        self.assertIn('other-actor', labels)
        self.assertNotIn('excluded', labels)

    def test_search_filter_handles_null_actor(self):
        """Search filter must also tolerate actor=None."""
        from unittest.mock import patch

        from workspace.core.services import activity as activity_service

        events_from_registry = [
            {'label': 'meeting', 'description': 'team sync',
             'actor': None, 'timestamp': None},
        ]

        with patch.object(
            activity_service.activity_registry,
            'get_recent_events',
            return_value=list(events_from_registry),
        ):
            result = activity_service.get_recent_events(
                viewer_id=1, search='meeting', limit=10,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['label'], 'meeting')
