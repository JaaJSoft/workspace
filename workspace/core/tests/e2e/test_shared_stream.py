"""The tabs of a browser share one SSE stream, held by a shared worker.

Whether the pages of a browser end up with one worker, and what becomes of it
when they go, is the browser's business. The unit tests run sse.js against an
imitation of it (``core/tests/js/sse_shared.test.js``); here a real browser
decides, and the checks are what the server sees - how many streams it is
asked for and by whom, when one is dropped, what a page asks on its way back.

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

from django.core.signals import request_started

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.core.views import sse

SNAPSHOT = re.compile(r"/api/v1/stream/snapshot(\?.*)?$")
# A request made by a worker names the worker's script as its referrer.
WORKER_SCRIPT = re.compile(r"/static/core/js/sse(\.[0-9a-f]+)?\.js$")

ALPINE = re.compile(r"/static/ui/js/vendor/alpine/alpine(\.[0-9a-f]+)?\.js$")

# How long the browser waits before reconnecting a stream the server ended.
RETRY_MS = 200
RETRY = f"retry: {RETRY_MS}\n\n"
# A write is how the server learns that the browser has gone.
TICK_SECONDS = 0.1
KEEPALIVE = ":\n\n"

TIMEOUT_MS = 15000
POLL_MS = 50

# Undefined rather than an error on a page where Alpine has not started yet.
UNREAD = "window.Alpine?.store('notifications')?.unread"

COUNT_RECONNECTS = """() => {
  window.__reconnects = 0;
  window.addEventListener('sse:reconnect', () => { window.__reconnects += 1; });
}"""
RECONNECTS = "() => window.__reconnects"

# What the browser fires on a page it puts in the back/forward cache, which
# Playwright's Chromium runs without.
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


class HeldRoutes:
    """Requests left unanswered until a test lets them through."""

    def __init__(self):
        self.routes = []

    def append_route(self, route):
        self.routes.append(route)


class Connection:
    """One stream the server was asked for."""

    def __init__(self, request):
        self.path = request.get_full_path()
        self.from_worker = bool(
            WORKER_SCRIPT.search(request.META.get("HTTP_REFERER", ""))
        )
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
        connection = Connection(request)
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

        # Every request for a stream, the refused ones included, and whether
        # each was refused for showing another user than the session's.
        self.stream_requests = []
        request_started.connect(self._note_request, weak=False)
        self.addCleanup(request_started.disconnect, self._note_request)
        self.refused_for_another_user = []
        decide = sse._shows_another_user

        def note_decision(request):
            refused = decide(request)
            self.refused_for_another_user.append(refused)
            return refused

        deciding = patch.object(sse, "_shows_another_user", note_decision)
        deciding.start()
        self.addCleanup(deciding.stop)

        # A page asks for the snapshot itself, which a route can answer.
        self.snapshots = []
        self.snapshot_events = []
        self.context.route(SNAPSHOT, self._serve_snapshot)

    def _note_request(self, sender, environ, **kwargs):
        if environ.get("PATH_INFO") == "/api/v1/stream":
            self.stream_requests.append(environ.get("QUERY_STRING", ""))

    def _serve_snapshot(self, route):
        self.snapshots.append(route.request.url)
        route.fulfill(json={"events": self.snapshot_events, "complete": True})

    def _until(self, condition, watcher, expected):
        """Wait on ``watcher``, any page still open, until ``condition`` holds."""
        for _ in range(TIMEOUT_MS // POLL_MS):
            if condition():
                return
            watcher.wait_for_timeout(POLL_MS)
        self.fail(f"{expected}: {self.stream_requests}")

    def _connection(self, number, watcher):
        """The number-th stream the server served, once it has."""
        self._until(
            lambda: len(self.stream.connections) >= number,
            watcher,
            f"expected {number} stream(s)",
        )
        return self.stream.connections[number - 1]

    def _dropped(self, connection):
        return connection.dropped.wait(TIMEOUT_MS / 1000)

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

        stream = self._connection(1, first)
        self.assertTrue(stream.from_worker)
        self.assertIn(f"user={self.user.pk}", stream.path)
        stream.send(_unread(7))

        self._wait_unread(first, 7)
        self._wait_unread(second, 7)
        self.assertEqual(len(self.stream.connections), 1)

    def test_a_tab_that_goes_costs_the_others_nothing(self):
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
        self.assertEqual(second.evaluate(RECONNECTS), 0)

    def test_a_tab_that_navigates_leaves_the_stream_alone(self):
        first = self._first_tab()
        second = self._next_tab()
        second.evaluate(COUNT_RECONNECTS)
        stream = self._connection(1, first)

        with first.expect_response(SNAPSHOT):
            first.goto(f"{self.live_server_url}/notes")
        stream.send(_unread(7))

        self._wait_unread(first, 7)
        self._wait_unread(second, 7)
        self.assertEqual(len(self.stream.connections), 1)
        self.assertEqual(second.evaluate(RECONNECTS), 0)

    def test_a_lone_tab_that_navigates_gets_its_stream_from_the_worker_again(self):
        tab = self._first_tab()
        left = self._connection(1, tab)

        tab.goto(f"{self.live_server_url}/notes")

        landed = self._connection(2, tab)
        self.assertTrue(landed.from_worker, "not a stream the page fell back to")
        self.assertTrue(self._dropped(left))
        self.assertEqual(
            self.snapshots, [], "the page starts its stream: nothing to ask"
        )

    def test_the_only_page_gives_the_stream_up_while_set_aside(self):
        tab = self._first_tab()
        left = self._connection(1, tab)
        left.send(_unread(7, event_id="cursor-1"))
        self._wait_unread(tab, 7)

        # The page is still there, and its worker with it: only the page
        # saying so makes the worker let go of the stream.
        tab.evaluate(SET_ASIDE)
        self.assertTrue(self._dropped(left))

        self.snapshot_events = [{"event": "notifications.count", "data": {"unread": 9}}]
        tab.evaluate(COUNT_RECONNECTS)
        with tab.expect_response(SNAPSHOT) as asked:
            tab.evaluate(BROUGHT_BACK)

        self.assertIn("last_event_id=cursor-1", asked.value.url)
        self._wait_unread(tab, 9)
        self.assertEqual(tab.evaluate(RECONNECTS), 1)
        self.assertTrue(self._connection(2, tab).from_worker)

    def test_a_page_back_next_to_a_running_stream_asks_what_it_missed(self):
        first = self._first_tab()
        second = self._next_tab()
        stream = self._connection(1, first)
        stream.send(_unread(7, event_id="cursor-1"))
        self._wait_unread(second, 7)

        second.evaluate(SET_ASIDE)
        stream.send(_unread(8, event_id="cursor-2"))
        self._wait_unread(first, 8)
        # Had the worker posted it to the page set aside too, it would be
        # there by now: both are posted in the same turn.
        second.wait_for_timeout(4 * POLL_MS)
        self.assertEqual(second.evaluate(f"() => {UNREAD}"), 7)

        self.snapshot_events = [{"event": "notifications.count", "data": {"unread": 9}}]
        second.evaluate(COUNT_RECONNECTS)
        with second.expect_response(SNAPSHOT) as asked:
            second.evaluate(BROUGHT_BACK)

        self.assertIn("last_event_id=cursor-1", asked.value.url)
        self._wait_unread(second, 9)
        self.assertEqual(second.evaluate(RECONNECTS), 1)
        self.assertEqual(len(self.stream.connections), 1)

    def test_a_stream_is_refused_to_another_account_and_resumed_for_its_own(self):
        tab = self._first_tab()
        stream = self._connection(1, tab)

        # Someone else signs in while the tab stays open: the worker's stream
        # reconnects with their cookie, and must not be handed their events.
        self.login_as(self.create_user(username="bob"))
        stream.end()
        self._until(lambda: len(self.stream_requests) >= 2, tab, "a reconnection")

        # A browser retries a dropped connection, never a refusal.
        tab.wait_for_timeout(5 * RETRY_MS)
        self.assertEqual(len(self.stream_requests), 2)
        self.assertEqual(len(self.stream.connections), 1, "refused, not served")
        # Refused for the account it shows, not for an error on the way.
        self.assertEqual(self.refused_for_another_user, [False, True])

        self.login_as(self.user)
        tab.evaluate(COUNT_RECONNECTS)
        tab.evaluate(SHOWN)

        self._connection(2, tab).send(_unread(7))
        self._wait_unread(tab, 7)
        self.assertEqual(tab.evaluate(RECONNECTS), 1)

    def test_a_worker_the_browser_ended_is_replaced(self):
        tab = self._first_tab()
        stream = self._connection(1, tab)
        stream.send(_unread(7, event_id="cursor-1"))
        self._wait_unread(tab, 7)

        # Nothing tells a page that its worker is gone. The browser is shared
        # by the tests of the class, and a worker goes a moment after its last
        # page: one of an earlier test may still be listed.
        devtools = self.browser.new_browser_cdp_session()
        self.addCleanup(devtools.detach)
        workers = [
            target["targetId"]
            for target in devtools.send("Target.getTargets")["targetInfos"]
            if target["type"] == "shared_worker"
        ]
        self.assertTrue(workers)
        for target_id in workers:
            devtools.send("Target.closeTarget", {"targetId": target_id})
        self.assertTrue(self._dropped(stream))

        self.snapshot_events = [{"event": "notifications.count", "data": {"unread": 9}}]
        tab.evaluate(COUNT_RECONNECTS)
        with tab.expect_response(SNAPSHOT, timeout=TIMEOUT_MS) as asked:
            tab.evaluate(SHOWN)

        self.assertIn("last_event_id=cursor-1", asked.value.url)
        self.assertTrue(self._connection(2, tab).from_worker)
        self._wait_unread(tab, 9)
        self.assertEqual(tab.evaluate(RECONNECTS), 1)

    def test_a_snapshot_that_beats_the_page_to_its_listeners_is_not_lost(self):
        self._first_tab()
        self.snapshot_events = [{"event": "notifications.count", "data": {"unread": 4}}]

        # The stores that listen are registered when Alpine starts. On a slow
        # machine the snapshot is answered first: Alpine is held back here
        # until it has been.
        held = HeldRoutes()
        second = self.context.new_page()
        second.route(ALPINE, held.append_route)
        second.goto(self.url, wait_until="commit")
        self._until(lambda: self.snapshots and held.routes, second, "a snapshot")
        second.wait_for_timeout(5 * POLL_MS)
        held.routes[0].continue_()

        self._wait_unread(second, 4)
