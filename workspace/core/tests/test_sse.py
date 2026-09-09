"""Tests for workspace.core.sse global SSE stream generators.

Validates that both the Pub/Sub and polling generators honor the
_MAX_CONNECTION_SECONDS budget — they must return cleanly so the browser
can auto-reconnect, and the finally blocks must run (pubsub cleanup,
Prometheus gauge decrement).
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from prometheus_client import REGISTRY

from workspace.core.views import sse

User = get_user_model()


def _sample(name, labels=None):
    """Return the current value of a Prometheus sample, or 0 if missing."""
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


class StreamMaxDurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="sse-user", password="p")

    def setUp(self):
        self.request = RequestFactory().get("/api/v1/stream")
        self.request.user = self.user

    def test_polling_stream_returns_when_max_duration_reached(self):
        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", -1),
            patch.object(sse, "_init_providers", return_value={}),
            patch("workspace.core.views.sse.time.sleep"),
        ):
            chunks = list(sse._event_stream_polling(self.request))

        # No providers -> no initial events; budget exhausted -> exit before any keepalive/poll.
        self.assertEqual(chunks, [])

    def test_pubsub_stream_returns_and_cleans_up_when_max_duration_reached(self):
        fake_pubsub = MagicMock()
        fake_pubsub.get_message.return_value = None
        fake_redis = MagicMock()
        fake_redis.pubsub.return_value = fake_pubsub

        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", -1),
            patch.object(sse, "_init_providers", return_value={}),
        ):
            chunks = list(sse._event_stream_pubsub(self.request, fake_redis))

        self.assertEqual(chunks, [])
        # finally block must run: subscribe was set up, unsubscribe + close must mirror it.
        fake_pubsub.subscribe.assert_called_once_with(f"sse:user:{self.user.id}")
        fake_pubsub.unsubscribe.assert_called_once_with(f"sse:user:{self.user.id}")
        fake_pubsub.close.assert_called_once()


class MetricsTests(TestCase):
    """Counters/Histograms exposed on /metrics by sse."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="sse-metrics", password="p")

    def setUp(self):
        self.request = RequestFactory().get("/api/v1/stream")
        self.request.user = self.user

    def test_emit_initial_events_increments_events_emitted(self):
        provider = MagicMock()
        provider.get_initial_events.return_value = [
            ("message_new", {"id": 1}, "evt-1"),
            ("message_new", {"id": 2}, "evt-2"),
        ]
        labels = {"provider": "chat", "event": "message_new"}
        before = _sample("sse_events_emitted_total", labels)

        chunks = list(sse._emit_initial_events({"chat": provider}, self.user.id, {}))

        self.assertEqual(len(chunks), 2)
        self.assertEqual(_sample("sse_events_emitted_total", labels) - before, 2)

    def test_poll_provider_observes_duration_and_increments_events(self):
        provider = MagicMock()
        provider.poll.return_value = [("updated", {}, "evt-3")]
        ev_labels = {"provider": "calendar", "event": "updated"}
        before_count = _sample(
            "sse_provider_poll_duration_seconds_count",
            {"provider": "calendar"},
        )
        before_events = _sample("sse_events_emitted_total", ev_labels)

        chunks = list(sse._poll_provider("calendar", provider, None, self.user.id, {}))

        self.assertEqual(len(chunks), 1)
        after_count = _sample(
            "sse_provider_poll_duration_seconds_count",
            {"provider": "calendar"},
        )
        self.assertEqual(after_count - before_count, 1)
        self.assertEqual(
            _sample("sse_events_emitted_total", ev_labels) - before_events, 1
        )

    def test_polling_stream_increments_forced_reconnect_when_budget_exhausted(self):
        before = _sample("sse_forced_reconnects_total", {"transport": "polling"})
        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", -1),
            patch.object(sse, "_init_providers", return_value={}),
            patch("workspace.core.views.sse.time.sleep"),
        ):
            list(sse._event_stream_polling(self.request))
        after = _sample("sse_forced_reconnects_total", {"transport": "polling"})
        self.assertEqual(after - before, 1)

    def test_pubsub_stream_increments_forced_reconnect_when_budget_exhausted(self):
        before = _sample("sse_forced_reconnects_total", {"transport": "pubsub"})
        fake_pubsub = MagicMock()
        fake_pubsub.get_message.return_value = None
        fake_redis = MagicMock()
        fake_redis.pubsub.return_value = fake_pubsub
        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", -1),
            patch.object(sse, "_init_providers", return_value={}),
        ):
            list(sse._event_stream_pubsub(self.request, fake_redis))
        after = _sample("sse_forced_reconnects_total", {"transport": "pubsub"})
        self.assertEqual(after - before, 1)

    def test_pubsub_stream_increments_pubsub_messages_on_real_message(self):
        # One real message, then None forever; budget cuts the loop on the 2nd iter.
        provider = MagicMock()
        provider.poll.return_value = []
        message = {"type": "message", "data": b'{"provider":"chat"}'}
        fake_pubsub = MagicMock()
        fake_pubsub.get_message.side_effect = [message, None, None, None]
        fake_redis = MagicMock()
        fake_redis.pubsub.return_value = fake_pubsub

        before = _sample("sse_pubsub_messages_total")

        # Use a tiny but positive budget so the first iteration runs, then we cut.
        # We patch monotonic to advance past the budget after the first message.
        times = iter([0.0, 0.0, 999.0, 999.0, 999.0])
        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", 1),
            patch.object(sse, "_init_providers", return_value={"chat": provider}),
            patch(
                "workspace.core.views.sse.time.monotonic",
                side_effect=lambda: next(times),
            ),
        ):
            list(sse._event_stream_pubsub(self.request, fake_redis))

        after = _sample("sse_pubsub_messages_total")
        self.assertEqual(after - before, 1)


class StreamConstantTests(TestCase):
    def test_max_connection_seconds_is_aligned_with_nginx_proxy_timeout(self):
        # nginx ingress proxy-read-timeout is 600s; staying below it avoids the
        # proxy cutting our stream while the budget is still ticking server-side.
        self.assertLessEqual(sse._MAX_CONNECTION_SECONDS, 600)
        # Anything under a minute is sub-calibrated: forces too many reconnects
        # which re-emit all initial snapshots and re-init providers.
        self.assertGreaterEqual(sse._MAX_CONNECTION_SECONDS, 60)


class StreamCursorTests(TestCase):
    """The single SSE id field has to carry every provider's resume point.

    A stream multiplexes all providers onto one ``id:``; the browser replays
    only the last one it saw, so an id naming a single provider would strand
    every other provider's position on a reconnect.
    """

    def test_cursor_round_trips_through_the_event_id(self):
        cursors = {"chat": "b0a1", "files": "42"}
        encoded = sse.encode_stream_cursor(cursors)
        self.assertNotIn("=", encoded)
        self.assertEqual(sse.decode_stream_cursor(encoded), cursors)

    def test_an_unusable_cursor_decodes_to_an_empty_map(self):
        for raw in ["", None, "not-base64!!", "*" * 600, sse.encode_stream_cursor([1])]:
            with self.subTest(raw=raw):
                self.assertEqual(sse.decode_stream_cursor(raw), {})

    def test_emitting_an_event_advances_only_its_own_provider(self):
        cursors = {"chat": "old-message"}
        chunk = sse._emit("files", "lock_released", {"a": 1}, "7", cursors)

        self.assertEqual(cursors, {"chat": "old-message", "files": "7"})
        event_id = next(
            line[4:] for line in chunk.split("\n") if line.startswith("id: ")
        )
        self.assertEqual(
            sse.decode_stream_cursor(event_id),
            {"chat": "old-message", "files": "7"},
        )

    def test_an_event_without_an_id_leaves_the_cursor_alone(self):
        cursors = {"files": "7"}
        chunk = sse._emit("chat", "typing", {}, None, cursors)

        self.assertEqual(cursors, {"files": "7"})
        self.assertNotIn("id: ", chunk)

    def test_providers_are_handed_their_own_resume_point(self):
        raw = sse.encode_stream_cursor({"chat": "msg-uuid", "files": "12"})
        request = RequestFactory().get("/api/v1/stream", HTTP_LAST_EVENT_ID=raw)
        self.assertEqual(
            sse._incoming_cursors(request), {"chat": "msg-uuid", "files": "12"}
        )

    def test_a_reopened_stream_may_pass_its_cursor_in_the_query_string(self):
        """A page-driven reconnect is a new EventSource - it sends no header."""
        raw = sse.encode_stream_cursor({"files": "12"})
        request = RequestFactory().get("/api/v1/stream", {"last_event_id": raw})
        self.assertEqual(sse._incoming_cursors(request), {"files": "12"})
