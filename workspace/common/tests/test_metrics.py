"""Tests for the re-import-safe Prometheus metric helpers."""

from django.test import TestCase
from prometheus_client import REGISTRY

from workspace.common.metrics import (
    safe_counter,
    safe_gauge,
    safe_histogram,
    safe_register,
    scrape_time_collectors,
)


class GetOrCreateTests(TestCase):
    def test_safe_counter_returns_same_instance_on_second_call(self):
        a = safe_counter("test_helper_counter_total", "doc", ["x"])
        b = safe_counter("test_helper_counter_total", "doc", ["x"])
        self.assertIs(a, b)

    def test_safe_gauge_returns_same_instance_on_second_call(self):
        a = safe_gauge("test_helper_gauge", "doc")
        b = safe_gauge("test_helper_gauge", "doc")
        self.assertIs(a, b)

    def test_safe_gauge_forwards_the_multiprocess_mode(self):
        gauge = safe_gauge(
            "test_helper_livesum_gauge", "doc", multiprocess_mode="livesum"
        )
        self.assertEqual(gauge._multiprocess_mode, "livesum")

    def test_safe_histogram_returns_same_instance_on_second_call(self):
        a = safe_histogram("test_helper_hist_seconds", "doc", ["y"])
        b = safe_histogram("test_helper_hist_seconds", "doc", ["y"])
        self.assertIs(a, b)

    def test_existing_instance_remains_usable(self):
        # The reused instance must still produce samples — i.e. we did not
        # accidentally swap it for a no-op.
        safe_counter("test_helper_usable_total", "doc", ["x"])  # first registration
        reused = safe_counter("test_helper_usable_total", "doc", ["x"])
        reused.labels(x="a").inc()
        sample = REGISTRY.get_sample_value(
            "test_helper_usable_total",
            {"x": "a"},
        )
        self.assertEqual(sample, 1.0)


class SafeRegisterTests(TestCase):
    def test_safe_register_swallows_duplicate_registration(self):
        from prometheus_client.core import GaugeMetricFamily

        class _DummyCollector:
            def collect(self):
                yield GaugeMetricFamily("test_helper_dummy", "doc", value=1)

        collector = _DummyCollector()
        safe_register(collector)
        # Second call must not raise.
        safe_register(collector)

    def test_registered_collector_is_listed_once_for_the_scrape_view(self):
        from prometheus_client.core import GaugeMetricFamily

        class _ListedCollector:
            def collect(self):
                yield GaugeMetricFamily("test_helper_listed", "doc", value=1)

        collector = _ListedCollector()
        safe_register(collector)
        safe_register(collector)
        listed = [c for c in scrape_time_collectors() if c is collector]
        self.assertEqual(len(listed), 1)
