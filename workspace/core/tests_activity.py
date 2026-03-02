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
