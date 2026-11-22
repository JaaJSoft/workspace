"""The tabs of a browser share one SSE stream, held by a shared worker.

Whether the pages of a browser end up with one worker, and what becomes of it
when they go, is the browser's business. The unit tests run sse.js against an
imitation of it (``core/tests/js/sse_shared.test.js``); here a real browser
decides, and the checks are what the server sees - how many streams it is
asked for, when one is dropped, where one resumes.

Playwright's routes never see what a shared worker requests, so the stream is
served for real, by a stand-in for the server's generator that records each
connection and sends what the test hands it.
"""

from __future__ import annotations

import json
import queue
import re
import threading
from unittest.mock import patch

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.core.views import sse

SNAPSHOT = re.compile(r"/api/v1/stream/snapshot(\?.*)?$")

# How long the browser waits before reconnecting a stream the server ended.
RETRY = "retry: 200\n\n"
# A write is how the server learns that the browser has gone.
TICK_SECONDS = 0.1
KEEPALIVE = ":\n\n"

TIMEOUT_MS = 15000
POLL_MS = 50

UNREAD = "Alpine.store('notifications').unread"

COUNT_RECONNECTS = """() => {
  window.__reconnects = 0;
  window.addEventListener('sse:reconnect', () => { window.__reconnects += 1; });
}"""

SET_ASIDE = "() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))"
BROUGHT_BACK = "() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))"
SHOWN = "() => document.dispatchEvent(new Event('visibilitychange'))"


def _event(name, data, event_id=None):
    lines = ["event: sse"]
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append("data: " + json.dumps({"event": name, "data": data}))
    return "\n".join(lines) + "\n\n"


def _unread(count, event_id=None):
    return _event("notifications.count", {"unread": count}, event_id)


class Connection:
    """One stream the server was asked for."""

    def __init__(self, path):
        self.path = path
        self.outbox = queue.SimpleQueue()
        self.dropped = threading.Event()

    def send(self, *chunks):
        for chunk in chunks:
            self.outbox.put(chunk)

    def end(self):
        """The server ends the stream, as it does every ten minutes."""
        self.outbox.put(None)


class StreamStandIn:
    """Stands in for ``sse._event_stream``."""

    def __init__(self):
        self.connections = []
        self.stopped = threading.Event()

    def __call__(self, request):
        connection = Connection(request.get_full_path())
        self.connections.append(connection)
        try:
            yield RETRY
            while not self.stopped.is_set():
                try:
                    chunk = connection.outbox.get(timeout=TICK_SECONDS)
                except queue.Empty:
                    chunk = KEEPALIVE
                if chunk is None:
                    return
                yield chunk
        finally:
            connection.dropped.set()


class SharedStreamTests(PlaywrightTestCase):
    STUB_GLOBAL_SSE = False

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.url = f"{self.live_server_url}/files"

        self.stream = StreamStandIn()
        patcher = patch.object(sse, "_event_stream", self.stream)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.stream.stopped.set)

        # A page asks for the snapshot itself, which a route can answer.
        self.snapshots = []
        self.snapshot_events = []
        self.context.route(SNAPSHOT, self._serve_snapshot)

    def _serve_snapshot(self, route):
        self.snapshots.append(route.request.url)
        route.fulfill(json={"events": self.snapshot_events})

    def _until(self, condition, watcher, expected):
        """Wait on ``watcher``, any page still open, until ``condition`` holds."""
        for _ in range(TIMEOUT_MS // POLL_MS):
            if condition():
                return
            watcher.wait_for_timeout(POLL_MS)
        self.fail(f"{expected}: {[c.path for c in self.stream.connections]}")

    def _connection(self, number, watcher):
        """The number-th stream the server was asked for, once it was."""
        self._until(
            lambda: len(self.stream.connections) >= number,
            watcher,
            f"expected {number} stream(s)",
        )
        return self.stream.connections[number - 1]

    def _first_tab(self):
        self.page.goto(self.url)
        self._connection(1, self.page)
        return self.page

    def _next_tab(self):
        page = self.context.new_page()
        with page.expect_response(SNAPSHOT):
            page.goto(self.url)
        return page

    def _wait_unread(self, page, count):
        page.wait_for_function(f"() => {UNREAD} === {count}")

    def test_two_tabs_share_one_stream(self):
        first = self._first_tab()
        self.snapshot_events = [{"event": "notifications.count", "data": {"unread": 4}}]
        second = self._next_tab()

        self._wait_unread(second, 4)
        self.assertEqual(
            len(self.snapshots), 1, "the tab that starts the stream reads none"
        )

        self._connection(1, first).send(_unread(7))

        self._wait_unread(first, 7)
        self._wait_unread(second, 7)
        self.assertEqual(len(self.stream.connections), 1)

    def test_a_tab_that_goes_takes_nothing_with_it(self):
        first = self._first_tab()
        second = self._next_tab()
        second.evaluate(COUNT_RECONNECTS)
        stream = self._connection(1, first)

        # The tab that started the worker.
        first.close()
        stream.send(_unread(7))

        self._wait_unread(second, 7)
        self.assertEqual(len(self.stream.connections), 1)
        self.assertFalse(stream.dropped.is_set())
        self.assertEqual(second.evaluate("() => window.__reconnects"), 0)

    def test_a_tab_that_navigates_leaves_the_stream_alone(self):
        first = self._first_tab()
        second = self._next_tab()
        stream = self._connection(1, first)

        with first.expect_response(SNAPSHOT):
            first.goto(f"{self.live_server_url}/notes")
        stream.send(_unread(7))

        self._wait_unread(first, 7)
        self._wait_unread(second, 7)
        self.assertEqual(len(self.stream.connections), 1)

    def test_the_stream_goes_with_the_last_tab(self):
        only = self._first_tab()
        stream = self._connection(1, only)

        only.close()

        self.assertTrue(stream.dropped.wait(TIMEOUT_MS / 1000))

    def test_a_single_tab_takes_its_stream_along_as_it_navigates(self):
        tab = self._first_tab()
        left = self._connection(1, tab)
        left.send(_unread(7, event_id="cursor-1"))
        self._wait_unread(tab, 7)

        tab.goto(f"{self.live_server_url}/notes")

        self._connection(2, tab)
        self.assertTrue(left.dropped.wait(TIMEOUT_MS / 1000))
        self.assertEqual(
            self.snapshots, [], "the page starts its stream: nothing to ask"
        )

    def test_a_page_back_from_the_bfcache_asks_what_it_missed(self):
        first = self._first_tab()
        second = self._next_tab()
        stream = self._connection(1, first)
        stream.send(_unread(7, event_id="cursor-1"))
        self._wait_unread(second, 7)

        second.evaluate(SET_ASIDE)
        stream.send(_unread(8, event_id="cursor-2"))
        self._wait_unread(first, 8)
        self.assertEqual(second.evaluate(f"() => {UNREAD}"), 7)

        self.snapshot_events = [{"event": "notifications.count", "data": {"unread": 9}}]
        second.evaluate(COUNT_RECONNECTS)
        with second.expect_response(SNAPSHOT) as asked:
            second.evaluate(BROUGHT_BACK)

        self.assertIn("last_event_id=cursor-1", asked.value.url)
        self._wait_unread(second, 9)
        self.assertEqual(second.evaluate("() => window.__reconnects"), 1)
        self.assertEqual(len(self.stream.connections), 1)

    def test_a_stream_the_server_refused_is_opened_again_for_a_tab_someone_looks_at(
        self,
    ):
        tab = self._first_tab()
        stream = self._connection(1, tab)

        # Signed out, the reconnection is answered 403: a browser retries a
        # dropped connection, never a refusal.
        self.context.clear_cookies()
        stream.end()
        self.assertTrue(stream.dropped.wait(TIMEOUT_MS / 1000))
        tab.wait_for_timeout(1500)
        self.login_as(self.user)
        tab.wait_for_timeout(1500)
        self.assertEqual(len(self.stream.connections), 1, "the browser gave up")

        tab.evaluate(COUNT_RECONNECTS)
        tab.evaluate(SHOWN)

        self._connection(2, tab).send(_unread(7))
        self._wait_unread(tab, 7)
        self.assertEqual(tab.evaluate("() => window.__reconnects"), 1)

    def test_a_worker_the_browser_ended_is_replaced_and_the_stream_resumed(self):
        tab = self._first_tab()
        stream = self._connection(1, tab)
        stream.send(_unread(7, event_id="cursor-1"))
        self._wait_unread(tab, 7)

        # Nothing tells a page that its worker is gone.
        devtools = self.browser.new_browser_cdp_session()
        workers = [
            target
            for target in devtools.send("Target.getTargets")["targetInfos"]
            if target["type"] == "shared_worker"
        ]
        self.assertEqual(len(workers), 1)
        devtools.send("Target.closeTarget", {"targetId": workers[0]["targetId"]})
        self.assertTrue(stream.dropped.wait(TIMEOUT_MS / 1000))

        tab.evaluate(COUNT_RECONNECTS)
        tab.evaluate(SHOWN)

        resumed = self._connection(2, tab)
        self.assertIn("last_event_id=cursor-1", resumed.path)
        resumed.send(_unread(8))
        self._wait_unread(tab, 8)
        self.assertEqual(tab.evaluate("() => window.__reconnects"), 1)
