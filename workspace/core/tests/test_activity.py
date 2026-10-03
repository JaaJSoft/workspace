from datetime import UTC, date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone as dj_timezone

from workspace.core.activity_registry import (
    ActivityProvider,
    ActivityProviderInfo,
    ActivityRegistry,
)
from workspace.core.services.activity import annotate_time_ago

User = get_user_model()


class StubProvider(ActivityProvider):
    """Minimal concrete provider for testing."""

    def __init__(self):
        self._daily_counts = {}
        self._events = []
        self._stats = {}

    def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
        return self._daily_counts

    def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
        return self._events[offset : offset + limit]

    def get_stats(self, user_id, *, viewer_id=None):
        return self._stats


class ActivityRegistryTests(TestCase):
    def setUp(self):
        self.registry = ActivityRegistry()

    def test_register_provider(self):
        info = ActivityProviderInfo(
            slug="files",
            label="Files",
            icon="hard-drive",
            provider_cls=StubProvider,
        )
        self.registry.register(info)
        self.assertIn("files", self.registry.get_all())

    def test_register_duplicate_slug_raises(self):
        info = ActivityProviderInfo(
            slug="files",
            label="Files",
            icon="hard-drive",
            provider_cls=StubProvider,
        )
        self.registry.register(info)
        with self.assertRaises(ValueError):
            self.registry.register(info)

    def test_get_provider_returns_instance(self):
        info = ActivityProviderInfo(
            slug="files",
            label="Files",
            icon="hard-drive",
            provider_cls=StubProvider,
        )
        self.registry.register(info)
        provider = self.registry.get_provider("files")
        self.assertIsInstance(provider, StubProvider)

    def test_get_provider_unknown_returns_none(self):
        self.assertIsNone(self.registry.get_provider("unknown"))

    def test_get_daily_counts_aggregates(self):
        class ProviderA(StubProvider):
            def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
                return {date(2026, 3, 1): 2, date(2026, 3, 2): 1}

        class ProviderB(StubProvider):
            def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
                return {date(2026, 3, 1): 3}

        self.registry.register(
            ActivityProviderInfo(
                slug="a",
                label="A",
                icon="a",
                provider_cls=ProviderA,
            )
        )
        self.registry.register(
            ActivityProviderInfo(
                slug="b",
                label="B",
                icon="b",
                provider_cls=ProviderB,
            )
        )
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
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [
                    {"label": "e1", "timestamp": ts1},
                    {"label": "e2", "timestamp": ts2},
                ]

        class ProviderB(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [{"label": "e3", "timestamp": ts3}]

        self.registry.register(
            ActivityProviderInfo(
                slug="a",
                label="A",
                icon="a",
                provider_cls=ProviderA,
            )
        )
        self.registry.register(
            ActivityProviderInfo(
                slug="b",
                label="B",
                icon="b",
                provider_cls=ProviderB,
            )
        )
        events = self.registry.get_recent_events(1, limit=3)
        labels = [e["label"] for e in events]
        self.assertEqual(labels, ["e2", "e3", "e1"])

    def test_get_recent_events_with_source_filter(self):
        from datetime import datetime

        from django.utils import timezone

        ts = timezone.make_aware(datetime(2026, 3, 1, 10, 0))

        class ProviderA(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [{"label": "from_a", "timestamp": ts}]

        class ProviderB(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [{"label": "from_b", "timestamp": ts}]

        self.registry.register(
            ActivityProviderInfo(
                slug="a",
                label="A",
                icon="a",
                provider_cls=ProviderA,
            )
        )
        self.registry.register(
            ActivityProviderInfo(
                slug="b",
                label="B",
                icon="b",
                provider_cls=ProviderB,
            )
        )
        events = self.registry.get_recent_events(1, source="a")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["label"], "from_a")

    def test_get_recent_events_offset_and_limit(self):
        from datetime import datetime, timedelta

        from django.utils import timezone

        base = timezone.make_aware(datetime(2026, 3, 1, 10, 0))

        class BigProvider(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [
                    {"label": f"e{i}", "timestamp": base + timedelta(hours=i)}
                    for i in range(5)
                ]

        self.registry.register(
            ActivityProviderInfo(
                slug="big",
                label="Big",
                icon="b",
                provider_cls=BigProvider,
            )
        )
        events = self.registry.get_recent_events(1, limit=2, offset=1)
        self.assertEqual(len(events), 2)
        # Sorted desc: e4, e3, e2, e1, e0 -> offset=1 -> e3, e2
        self.assertEqual(events[0]["label"], "e3")
        self.assertEqual(events[1]["label"], "e2")

    def test_get_stats_aggregates(self):
        class ProviderA(StubProvider):
            def get_stats(self, user_id, *, viewer_id=None):
                return {"total_files": 42}

        class ProviderB(StubProvider):
            def get_stats(self, user_id, *, viewer_id=None):
                return {"total_messages": 100}

        self.registry.register(
            ActivityProviderInfo(
                slug="a",
                label="A",
                icon="a",
                provider_cls=ProviderA,
            )
        )
        self.registry.register(
            ActivityProviderInfo(
                slug="b",
                label="B",
                icon="b",
                provider_cls=ProviderB,
            )
        )
        stats = self.registry.get_stats(1)
        self.assertEqual(
            stats, {"a": {"total_files": 42}, "b": {"total_messages": 100}}
        )

    def test_get_daily_counts_empty_registry(self):
        counts = self.registry.get_daily_counts(1, date(2026, 3, 1), date(2026, 3, 2))
        self.assertEqual(counts, {})

    def test_get_recent_events_empty_registry(self):
        events = self.registry.get_recent_events(1)
        self.assertEqual(events, [])

    def test_get_stats_empty_registry(self):
        stats = self.registry.get_stats(1)
        self.assertEqual(stats, {})

    def test_exclude_actor_id_is_forwarded_to_providers(self):
        """Exclusion happens inside each provider's query, before its slice.

        Filtering after the slice let a burst of the excluded actor's events
        fill every provider's window and hide older events by others.
        """
        received = []

        class RecordingProvider(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                received.append(kwargs.get("exclude_actor_id"))
                return []

        self.registry.register(
            ActivityProviderInfo(
                slug="rec", label="R", icon="r", provider_cls=RecordingProvider
            )
        )

        self.registry.get_recent_events(None, limit=5, exclude_actor_id=1)
        self.registry.get_recent_events(None, limit=5, source="rec", exclude_actor_id=1)
        self.registry.get_recent_events(None, limit=5)
        self.assertEqual(received, [1, 1, None])

    def test_provider_exception_is_handled_gracefully(self):
        class FailingProvider(ActivityProvider):
            def get_daily_counts(self, user_id, date_from, date_to, *, viewer_id=None):
                raise RuntimeError("boom")

            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                raise RuntimeError("boom")

            def get_stats(self, user_id, *, viewer_id=None):
                raise RuntimeError("boom")

        self.registry.register(
            ActivityProviderInfo(
                slug="fail",
                label="Fail",
                icon="x",
                provider_cls=FailingProvider,
            )
        )
        counts = self.registry.get_daily_counts(1, date(2026, 3, 1), date(2026, 3, 2))
        self.assertEqual(counts, {})
        events = self.registry.get_recent_events(1)
        self.assertEqual(events, [])
        stats = self.registry.get_stats(1)
        self.assertEqual(stats, {"fail": {}})

    def test_events_take_the_module_color_from_the_module_registry(self):
        from django.utils import timezone

        ts = timezone.make_aware(datetime(2026, 3, 1, 10, 0))

        class FilesProvider(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [{"label": "f", "timestamp": ts}]

        class OrphanProvider(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [{"label": "o", "timestamp": ts}]

        self.registry.register(
            ActivityProviderInfo(
                slug="files", label="Files", icon="f", provider_cls=FilesProvider
            )
        )
        self.registry.register(
            ActivityProviderInfo(
                slug="no-such-module", label="X", icon="x", provider_cls=OrphanProvider
            )
        )
        colors = {
            e["label"]: e["source_color"] for e in self.registry.get_recent_events(1)
        }
        self.assertEqual(colors, {"f": "indigo", "o": "slate"})


class ActivityRegistryAllowedSourcesTests(TestCase):
    def setUp(self):
        self.registry = ActivityRegistry()

        from datetime import datetime

        from django.utils import timezone

        ts = timezone.make_aware(datetime(2026, 3, 1, 10, 0))

        class ProviderA(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [{"label": "from_a", "timestamp": ts}]

        class ProviderB(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [{"label": "from_b", "timestamp": ts}]

        self.registry.register(
            ActivityProviderInfo(
                slug="a",
                label="A",
                icon="a",
                provider_cls=ProviderA,
            )
        )
        self.registry.register(
            ActivityProviderInfo(
                slug="b",
                label="B",
                icon="b",
                provider_cls=ProviderB,
            )
        )

    def test_allowed_sources_limits_all_mode_fanout(self):
        events = self.registry.get_recent_events(1, limit=10, allowed_sources={"a"})
        self.assertEqual([e["label"] for e in events], ["from_a"])

    def test_allowed_sources_none_keeps_every_provider(self):
        events = self.registry.get_recent_events(1, limit=10, allowed_sources=None)
        self.assertEqual({e["label"] for e in events}, {"from_a", "from_b"})


class ActivityModuleVisibilityTests(TestCase):
    """Preview-module activity must stay hidden from users who cannot see the
    module itself (dashboard and profile UI: source tabs and the ALL feed)."""

    def setUp(self):
        from datetime import datetime

        from django.utils import timezone

        self.normal = User.objects.create_user(username="vis-n", password="x")
        self.staff = User.objects.create_user(
            username="vis-s", password="x", is_staff=True
        )

        ts = timezone.make_aware(datetime(2026, 3, 1, 10, 0))

        class FilesProvider(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [{"label": "files-event", "timestamp": ts}]

        class LabProvider(StubProvider):
            def get_recent_events(self, user_id, limit=10, offset=0, **kwargs):
                return [{"label": "lab-event", "timestamp": ts}]

        self.activity_registry = ActivityRegistry()
        self.activity_registry.register(
            ActivityProviderInfo(
                slug="files",
                label="Files",
                icon="f",
                provider_cls=FilesProvider,
            )
        )
        self.activity_registry.register(
            ActivityProviderInfo(
                slug="lab",
                label="Lab",
                icon="l",
                provider_cls=LabProvider,
            )
        )

        from workspace.core.module_registry import ModuleInfo

        def _module(slug, preview=False):
            return ModuleInfo(
                name=slug.title(),
                slug=slug,
                description="",
                icon="i",
                color="indigo",
                url=f"/{slug}",
                active=True,
                preview=preview,
            )

        self._modules = {
            "files": _module("files"),
            "lab": _module("lab", preview=True),
        }

    def _patched(self):
        from contextlib import ExitStack
        from unittest.mock import patch

        from workspace.core.services import activity as activity_service

        stack = ExitStack()
        stack.enter_context(
            patch.object(activity_service, "activity_registry", self.activity_registry)
        )
        mock_mod_registry = stack.enter_context(
            patch("workspace.core.services.module_visibility.registry")
        )
        mock_mod_registry.get_all.return_value = list(self._modules.values())
        mock_mod_registry.get.side_effect = self._modules.get
        return stack

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_get_sources_hides_preview_module_from_normal_user(self):
        from workspace.core.services import activity as activity_service

        with self._patched():
            slugs = [s["slug"] for s in activity_service.get_sources(self.normal)]
        self.assertEqual(slugs, ["files"])

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_get_sources_keeps_preview_module_for_staff(self):
        from workspace.core.services import activity as activity_service

        with self._patched():
            slugs = [s["slug"] for s in activity_service.get_sources(self.staff)]
        self.assertEqual(slugs, ["files", "lab"])

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_get_sources_without_user_keeps_all(self):
        from workspace.core.services import activity as activity_service

        with self._patched():
            slugs = [s["slug"] for s in activity_service.get_sources()]
        self.assertEqual(slugs, ["files", "lab"])

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_all_feed_excludes_hidden_module_events_for_normal_user(self):
        from workspace.core.services import activity as activity_service

        with self._patched():
            events = activity_service.get_recent_events(
                viewer_id=self.normal.id,
                visible_to=self.normal,
                limit=10,
            )
        self.assertEqual([e["label"] for e in events], ["files-event"])

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_all_feed_keeps_hidden_module_events_for_staff(self):
        from workspace.core.services import activity as activity_service

        with self._patched():
            events = activity_service.get_recent_events(
                viewer_id=self.staff.id,
                visible_to=self.staff,
                limit=10,
            )
        self.assertEqual({e["label"] for e in events}, {"files-event", "lab-event"})

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_explicit_hidden_source_returns_empty_for_normal_user(self):
        from workspace.core.services import activity as activity_service

        with self._patched():
            events = activity_service.get_recent_events(
                viewer_id=self.normal.id,
                visible_to=self.normal,
                source="lab",
                limit=10,
            )
        self.assertEqual(events, [])

    @override_settings(PREVIEW_VISIBILITY="staff")
    def test_without_visible_to_all_events_survive(self):
        from workspace.core.services import activity as activity_service

        with self._patched():
            events = activity_service.get_recent_events(
                viewer_id=self.normal.id,
                limit=10,
            )
        self.assertEqual({e["label"] for e in events}, {"files-event", "lab-event"})


class ActivityServiceTests(TestCase):
    """Tests for workspace.core.services.activity."""

    def test_exclude_user_id_is_pushed_down_without_over_fetching(self):
        """The service forwards the exclusion and lets the providers paginate."""
        from unittest.mock import patch

        from workspace.core.services import activity as activity_service

        with patch.object(
            activity_service.activity_registry,
            "get_recent_events",
            return_value=[{"label": "kept", "actor": {"id": 7}, "timestamp": None}],
        ) as registry_call:
            result = activity_service.get_recent_events(
                viewer_id=7,
                exclude_user_id=7,
                limit=10,
                offset=20,
            )

        kwargs = registry_call.call_args.kwargs
        self.assertEqual(kwargs["exclude_actor_id"], 7)
        self.assertEqual((kwargs["limit"], kwargs["offset"]), (10, 20))
        self.assertEqual([e["label"] for e in result], ["kept"])

    def test_search_filter_handles_null_actor(self):
        """Search filter must also tolerate actor=None."""
        from unittest.mock import patch

        from workspace.core.services import activity as activity_service

        events_from_registry = [
            {
                "label": "meeting",
                "description": "team sync",
                "actor": None,
                "timestamp": None,
            },
        ]

        with patch.object(
            activity_service.activity_registry,
            "get_recent_events",
            return_value=list(events_from_registry),
        ):
            result = activity_service.get_recent_events(
                viewer_id=1,
                search="meeting",
                limit=10,
            )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["label"], "meeting")


class UsageStatsCacheTests(TestCase):
    """Tests for the cached get_usage_stats wrapper.

    The wrapper exists to keep the dashboard / profile hot path from
    re-running the full per-provider stats fan-out on every request. It
    delegates to the registry and memoizes the result for a short TTL.
    """

    # LocMemCache is process-global and NOT reset between TestCase runs; clear
    # it both ways so stats cached by earlier tests (via pk reuse) don't serve
    # these asserts, and ours don't leak into later tests.
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    def test_delegates_to_registry_and_passes_args(self):
        from unittest.mock import patch

        from workspace.core.services import activity as activity_service

        with patch.object(
            activity_service.activity_registry,
            "get_stats",
            return_value={"files": {"total_files": 7}},
        ) as mock_stats:
            result = activity_service.get_usage_stats(1)

        self.assertEqual(result, {"files": {"total_files": 7}})
        mock_stats.assert_called_once_with(1, viewer_id=None)

    def test_second_call_within_ttl_hits_cache(self):
        from unittest.mock import patch

        from workspace.core.services import activity as activity_service

        with patch.object(
            activity_service.activity_registry,
            "get_stats",
            return_value={"files": {"total_files": 7}},
        ) as mock_stats:
            first = activity_service.get_usage_stats(1)
            second = activity_service.get_usage_stats(1)

        self.assertEqual(first, second)
        mock_stats.assert_called_once()  # second read served from cache

    def test_distinct_cache_entry_per_user_and_viewer(self):
        from unittest.mock import patch

        from workspace.core.services import activity as activity_service

        with patch.object(
            activity_service.activity_registry,
            "get_stats",
            return_value={},
        ) as mock_stats:
            activity_service.get_usage_stats(1)
            activity_service.get_usage_stats(2)
            activity_service.get_usage_stats(1, viewer_id=9)

        self.assertEqual(mock_stats.call_count, 3)


class AnnotateTimeAgoTimezoneTests(TestCase):
    def tearDown(self):
        dj_timezone.deactivate()

    def test_month_label_uses_active_timezone(self):
        from unittest.mock import patch

        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        ts = datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
        dj_timezone.activate("Europe/Paris")
        # Freeze "now" in the same year so the label stays year-less
        # regardless of when the test runs.
        fixed_now = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
        with patch("django.utils.timezone.now", return_value=fixed_now):
            events = annotate_time_ago([{"timestamp": ts}])
        self.assertEqual(events[0]["time_ago"], "Feb 01")


class ExcludedActorBurstTests(TestCase):
    """A burst of the viewer's own events must not hide older events by others."""

    def setUp(self):
        from workspace.files.models import File

        self.alice = User.objects.create_user(username="alice", password="pass123")
        self.bob = User.objects.create_user(username="bob", password="pass123")
        self.file = File.objects.create(
            owner=self.alice,
            name="report.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            size=10,
        )

    def _seed(self, own_events):
        from datetime import timedelta

        from workspace.files.models import FileEvent

        now = dj_timezone.now()
        bob_event = FileEvent.objects.create(
            file=self.file, actor=self.bob, action=FileEvent.Action.CONTENT_REPLACED
        )
        FileEvent.objects.filter(pk=bob_event.pk).update(
            created_at=now - timedelta(days=1)
        )
        for i in range(own_events):
            own = FileEvent.objects.create(
                file=self.file,
                actor=self.alice,
                action=FileEvent.Action.CONTENT_REPLACED,
            )
            FileEvent.objects.filter(pk=own.pk).update(
                created_at=now - timedelta(minutes=i)
            )

    def _feed(self, **kwargs):
        from workspace.core.services import activity as activity_service

        return activity_service.get_recent_events(
            viewer_id=self.alice.id,
            exclude_user_id=self.alice.id,
            visible_to=self.alice,
            **kwargs,
        )

    def test_other_actor_event_survives_a_large_burst(self):
        self._seed(own_events=70)
        events = self._feed(limit=11)
        self.assertEqual([e["actor"]["id"] for e in events], [self.bob.id])

    def test_other_actor_event_survives_a_large_burst_in_single_source(self):
        self._seed(own_events=70)
        events = self._feed(limit=11, source="files")
        self.assertEqual([e["actor"]["id"] for e in events], [self.bob.id])
