"""Tests for workspace.core.sse global SSE stream generators.

Validates that both the Pub/Sub and polling generators honor the
_MAX_CONNECTION_SECONDS budget — they must return cleanly so the browser
can auto-reconnect, and the finally blocks must run (pubsub cleanup,
Prometheus gauge decrement).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import orjson
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone, translation
from prometheus_client import REGISTRY

from workspace.core.sse_registry import push_user_event
from workspace.core.views import sse
from workspace.imports.sse_provider import ImportsSSEProvider
from workspace.users.services.settings import set_setting

User = get_user_model()


def _sample(name, labels=None):
    """Return the current value of a Prometheus sample, or 0 if missing."""
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


def _fake_hub(*notifications):
    """A hub whose subscription hands out *notifications*, then nothing."""
    hub = MagicMock()
    remaining = iter(notifications)
    hub.subscribe.return_value.wait.side_effect = lambda timeout: next(remaining, set())
    return hub


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
        hub = _fake_hub()

        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", -1),
            patch.object(sse, "_init_providers", return_value={}),
        ):
            chunks = list(sse._event_stream_pubsub(self.request, hub))

        self.assertEqual(chunks, [])
        # finally block must run: the subscription is closed as it was opened.
        hub.subscribe.assert_called_once_with(self.user.id)
        hub.subscribe.return_value.close.assert_called_once()

    def test_pubsub_stream_subscribes_before_building_its_initial_events(self):
        """A notification sent while the initial events are built must not
        fall between them and the subscription."""
        order = []
        hub = _fake_hub()
        subscription = hub.subscribe.return_value
        hub.subscribe.side_effect = lambda user_id: (
            order.append("subscribe") or subscription
        )
        provider = MagicMock()
        provider.get_initial_events.side_effect = lambda: order.append("initial") or []

        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", -1),
            patch.object(sse, "_init_providers", return_value={"chat": provider}),
        ):
            list(sse._event_stream_pubsub(self.request, hub))

        self.assertEqual(order, ["subscribe", "initial"])


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
        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", -1),
            patch.object(sse, "_init_providers", return_value={}),
        ):
            list(sse._event_stream_pubsub(self.request, _fake_hub()))
        after = _sample("sse_forced_reconnects_total", {"transport": "pubsub"})
        self.assertEqual(after - before, 1)

    def test_pubsub_stream_increments_pubsub_messages_on_real_message(self):
        # One notification, then nothing; budget cuts the loop on the 2nd iter.
        provider = MagicMock()
        provider.poll.return_value = []
        hub = _fake_hub({"chat"})

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
            list(sse._event_stream_pubsub(self.request, hub))

        after = _sample("sse_pubsub_messages_total")
        self.assertEqual(after - before, 1)
        # Polled for the notification, with a dirty value rather than None.
        provider.poll.assert_called_once()
        self.assertIsNotNone(provider.poll.call_args.args[0])


class StreamDatabaseConnectionTests(TestCase):
    """A stream spends most of its ten minutes waiting, and must not hold a
    database connection meanwhile: on PostgreSQL the pool gives each process
    four, so a handful of open tabs would leave none for any other request.

    The pool cannot be reproduced on the SQLite test database, so the contract
    is checked where it is kept: the connection is handed back before every
    wait.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="sse-db", password="p")

    def setUp(self):
        self.request = RequestFactory().get("/api/v1/stream")
        self.request.user = self.user

    def test_pubsub_stream_releases_the_connection_before_each_wait(self):
        calls = []
        hub = MagicMock()
        hub.subscribe.return_value.wait.side_effect = lambda timeout: (
            calls.append("wait") or set()
        )
        # Two rounds, then the budget ends the stream.
        times = iter([0.0, 0.0, 0.0, 0.0, 0.0, 999.0])
        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", 1),
            patch.object(sse, "_init_providers", return_value={}),
            patch.object(
                sse,
                "close_old_connections",
                side_effect=lambda: calls.append("release"),
            ),
            patch(
                "workspace.core.views.sse.time.monotonic",
                side_effect=lambda: next(times),
            ),
        ):
            list(sse._event_stream_pubsub(self.request, hub))

        self.assertEqual(calls, ["release", "wait", "release", "wait"])

    def test_polling_stream_releases_the_connection_before_each_wait(self):
        calls = []
        times = iter([0.0, 0.0, 0.0, 0.0, 999.0, 999.0])
        # The stream's clock alone: patching time.time itself would also feed
        # this script to prometheus_client, which reads it when it creates a
        # labelled metric for the first time in the process.
        clock = SimpleNamespace(
            time=lambda: next(times), sleep=lambda seconds: calls.append("wait")
        )
        with (
            patch.object(sse, "_MAX_CONNECTION_SECONDS", 1),
            patch.object(sse, "_init_providers", return_value={}),
            patch.object(
                sse,
                "close_old_connections",
                side_effect=lambda: calls.append("release"),
            ),
            patch.object(sse, "time", clock),
        ):
            list(sse._event_stream_polling(self.request))

        self.assertEqual(calls, ["release", "wait"])


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


def _payloads(chunks):
    """The JSON payload of each SSE chunk, as the page parses it."""
    return [
        orjson.loads(line[len("data: ") :])
        for chunk in chunks
        for line in chunk.split("\n")
        if line.startswith("data: ")
    ]


class StreamSnapshotTests(TestCase):
    """A browser holds one stream for all its tabs; a tab that joins it later
    asks here for the events a stream starts with."""

    URL = "/api/v1/stream/snapshot"

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="sse-snapshot", password="p")

    def tearDown(self):
        cache.clear()

    def test_anonymous_is_refused_like_on_the_stream(self):
        self.assertEqual(self.client.get(self.URL).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/stream").status_code, 403)

    def test_answers_a_get_only(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(self.URL).status_code, 405)

    def test_answers_what_a_stream_opens_with(self):
        """Compared on the registered providers: a new one is covered as it comes."""
        providers = sse._init_providers(self.user, {})
        self.assertTrue(providers)
        opening = _payloads(sse._emit_initial_events(providers, self.user.id, {}))
        self.assertTrue(opening)

        self.client.force_login(self.user)
        response = self.client.get(self.URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response.json(), {"events": opening, "complete": True})

    def test_a_failing_provider_costs_the_others_nothing(self):
        broken = MagicMock()
        broken.get_initial_events.side_effect = RuntimeError("down")
        working = MagicMock()
        working.get_initial_events.return_value = [("count", {"unread": 4}, "evt-1")]
        self.client.force_login(self.user)

        with (
            patch.object(
                sse,
                "_init_providers",
                return_value={"chat": broken, "notifications": working},
            ),
            self.assertLogs("workspace.core.views.sse", level="ERROR"),
        ):
            response = self.client.get(self.URL)

        # Not complete: the page must not take the stream's position for its
        # own, which would skip what the failed provider had for it.
        self.assertEqual(
            response.json(),
            {
                "events": [{"event": "notifications.count", "data": {"unread": 4}}],
                "complete": False,
            },
        )

    def test_a_provider_that_cannot_start_leaves_the_answer_incomplete(self):
        self.client.force_login(self.user)
        with (
            patch.object(
                ImportsSSEProvider, "__init__", side_effect=RuntimeError("down")
            ),
            self.assertLogs("workspace.core.views.sse", level="ERROR"),
        ):
            response = self.client.get(self.URL)

        self.assertIs(response.json()["complete"], False)
        self.assertTrue(response.json()["events"], "the other providers answered")

    def test_a_tab_coming_back_is_given_what_was_queued_since_its_cursor(self):
        """A page restored from the bfcache joins a stream that kept running,
        and the snapshot is all it reads of what it missed: without the replay
        a ``lock_released`` sent meanwhile never reaches it, and its editor
        stays read-only."""
        released = {"type": "lock_released", "file_uuid": "f-1", "actor": "bob"}
        push_user_event("files", self.user.id, released)
        self.client.force_login(self.user)

        def names(response):
            return [event["event"] for event in response.json()["events"]]

        # A page that has just rendered already reflects it.
        self.assertNotIn("files.lock_released", names(self.client.get(self.URL)))

        cursor = sse.encode_stream_cursor({"files": "0"})
        back = self.client.get(self.URL, {"last_event_id": cursor})
        self.assertIn(
            {"event": "files.lock_released", "data": released},
            back.json()["events"],
        )

    def test_reading_consumes_nothing(self):
        """The stream must still find what a snapshot read."""
        push_user_event("files", self.user.id, {"type": "lock_released"})
        cursor = sse.encode_stream_cursor({"files": "0"})
        self.client.force_login(self.user)

        first = self.client.get(self.URL, {"last_event_id": cursor}).json()
        second = self.client.get(self.URL, {"last_event_id": cursor}).json()
        self.assertEqual(first, second)

        provider = sse._init_providers(self.user, {"files": "0"})["files"]
        self.assertEqual(len(provider.get_initial_events()), 1)

    def test_built_in_the_zone_and_language_a_stream_runs_in(self):
        """A stream body runs once the middlewares have left, in UTC and in the
        instance's language. An event built otherwise here would read
        differently in a tab depending on when it joined the stream."""
        set_setting(self.user, "core", "timezone", "Asia/Tokyo")
        set_setting(self.user, "core", "language", "fr")
        seen = {}

        def initial_events():
            seen["zone"] = timezone.get_current_timezone_name()
            seen["language"] = translation.get_language()
            return []

        provider = MagicMock()
        provider.get_initial_events.side_effect = initial_events
        self.client.force_login(self.user)

        with patch.object(sse, "_init_providers", return_value={"chat": provider}):
            self.client.get(self.URL)

        self.assertEqual(seen, {"zone": "UTC", "language": "en"})

    def test_is_revalidated_on_every_read(self):
        self.client.force_login(self.user)
        response = self.client.get(self.URL)
        self.assertEqual(response["Cache-Control"], "private, no-cache")


class ShownUserTests(TestCase):
    """A tab outlives its session. Its stream then reconnects with the cookie
    of whoever signed in since, and would hand that account's events to a page
    of the first one: the page says who it shows, and is refused anyone else's.
    """

    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="sse-alice", password="p")
        cls.bob = User.objects.create_user(username="sse-bob", password="p")

    def _status(self, url, shown=None):
        query = {} if shown is None else {"user": shown}
        with patch.object(sse, "_event_stream", return_value=iter(())):
            return self.client.get(url, query).status_code

    def test_another_users_page_is_refused(self):
        self.client.force_login(self.bob)
        for url in ("/api/v1/stream", "/api/v1/stream/snapshot"):
            with self.subTest(url=url):
                self.assertEqual(self._status(url, shown=self.alice.pk), 403)
                self.assertEqual(self._status(url, shown="nobody"), 403)

    def test_the_users_own_page_is_answered(self):
        self.client.force_login(self.alice)
        for url in ("/api/v1/stream", "/api/v1/stream/snapshot"):
            with self.subTest(url=url):
                self.assertEqual(self._status(url, shown=self.alice.pk), 200)

    def test_a_page_that_names_nobody_is_answered(self):
        self.client.force_login(self.alice)
        for url in ("/api/v1/stream", "/api/v1/stream/snapshot"):
            with self.subTest(url=url):
                self.assertEqual(self._status(url), 200)


@override_settings(PREVIEW_VISIBILITY="staff")
class StreamModuleVisibilityTests(TestCase):
    """The stream is served by core, so the module guard never sees it: a
    provider of a module the user may not reach is not started at all."""

    def test_a_hidden_module_provides_no_events(self):
        regular = User.objects.create_user(username="regular", password="p")
        self.assertNotIn("imports", sse._init_providers(regular, {}))

    def test_the_audience_gets_them(self):
        staff = User.objects.create_user(username="staff", password="p", is_staff=True)
        self.assertIn("imports", sse._init_providers(staff, {}))

    def test_a_snapshot_leaves_a_hidden_module_out_too(self):
        regular = User.objects.create_user(username="regular", password="p")
        staff = User.objects.create_user(username="staff", password="p", is_staff=True)
        job = {"type": "job", "state": "running"}

        def names(user):
            self.client.force_login(user)
            events = self.client.get("/api/v1/stream/snapshot").json()["events"]
            return [event["event"] for event in events]

        with patch.object(
            ImportsSSEProvider, "get_initial_events", return_value=[("job", job, None)]
        ):
            self.assertIn("imports.job", names(staff))
            self.assertNotIn("imports.job", names(regular))
