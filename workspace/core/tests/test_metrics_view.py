"""The /metrics view in single-process and multiprocess mode."""

import base64
import os
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from prometheus_client import Counter, values
from prometheus_client.core import GaugeMetricFamily

from workspace import celery as celery_module
from workspace.common.metrics import scrape_time_collectors
from workspace.core.views.sse import SSE_CONNECTIONS

AUTH = "Basic " + base64.b64encode(b"prom:s3cret").decode()


def _record_in_worker(pid, amount):
    """Increment a counter the way gunicorn worker ``pid`` would."""
    with patch.object(values, "ValueClass", values.MultiProcessValue(lambda: pid)):
        Counter("test_view_worker_requests", "doc", registry=None).inc(amount)


class _FakeQueueCollector:
    def collect(self):
        yield GaugeMetricFamily("test_view_queue_length", "doc", value=7)


@override_settings(METRICS_USER="prom", METRICS_PASSWORD="s3cret")
class MultiprocessMetricsViewTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        env = patch.dict(os.environ, {"PROMETHEUS_MULTIPROC_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)

    def _scrape(self):
        resp = self.client.get("/metrics", HTTP_AUTHORIZATION=AUTH)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def test_counters_are_summed_across_workers(self):
        _record_in_worker(101, 3)
        _record_in_worker(102, 4)
        self.assertIn("test_view_worker_requests_total 7.0", self._scrape())

    def test_scrape_time_collectors_are_kept(self):
        with patch(
            "workspace.core.views.metrics.scrape_time_collectors",
            return_value=(_FakeQueueCollector(),),
        ):
            self.assertIn("test_view_queue_length 7.0", self._scrape())


@override_settings(METRICS_USER="prom", METRICS_PASSWORD="s3cret")
class SingleProcessMetricsViewTests(SimpleTestCase):
    def test_default_registry_is_served(self):
        with patch.dict(os.environ):
            os.environ.pop("PROMETHEUS_MULTIPROC_DIR", None)
            resp = self.client.get("/metrics", HTTP_AUTHORIZATION=AUTH)
        self.assertIn(b"sse_active_connections", resp.content)


class MultiprocessDeclarationTests(SimpleTestCase):
    def test_celery_queue_length_is_computed_at_scrape_time(self):
        self.assertTrue(
            any(
                isinstance(c, celery_module._CeleryQueueLengthCollector)
                for c in scrape_time_collectors()
            )
        )

    def test_sse_gauge_sums_live_workers_only(self):
        self.assertEqual(SSE_CONNECTIONS._multiprocess_mode, "livesum")
