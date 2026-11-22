"""The tabs of a browser share one SSE stream.

Who holds the stream is decided by the browser: a Web Lock picks the tab, a
BroadcastChannel carries its events to the others. The unit tests run sse.js
against an imitation of both (``core/tests/js/sse_shared.test.js``); here the
real ones decide, and the checks are what the server would see - which tab
asks for the stream, which one asks for a snapshot instead, and where a stream
that changes hands resumes.

No stream reaches the live server: each request is held, and answered by the
test with the events it wants delivered.
"""

from __future__ import annotations

import json
import re

from workspace.common.tests.e2e.base import PlaywrightTestCase

STREAM = re.compile(r"/api/v1/stream(\?.*)?$")
SNAPSHOT = re.compile(r"/api/v1/stream/snapshot$")

# Long enough that EventSource never reconnects on its own during a test: a
# held request answered in one piece ends there.
QUIET = "retry: 3600000\n\n"

# A tab gives a silent leader three seconds before taking the stream over.
STREAM_TIMEOUT_MS = 15000
POLL_MS = 50

UNREAD = "Alpine.store('notifications').unread"

COUNT_RECONNECTS = """() => {
  window.__reconnects = 0;
  window.addEventListener('sse:reconnect', () => { window.__reconnects += 1; });
}"""

# What a tab the browser suspended looks like to the others: it holds the lock
# and answers nothing.
HOLD_LOCK_SILENTLY = """(name) => new Promise((granted) => {
  navigator.locks.request(name, () => {
    granted();
    return new Promise(() => {});
  });
})"""

SET_ASIDE = "() => window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }))"
BROUGHT_BACK = "() => window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }))"


def _event(name, data, event_id=None):
    lines = ["event: sse"]
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append("data: " + json.dumps({"event": name, "data": data}))
    return "\n".join(lines) + "\n\n"


class SharedStreamTests(PlaywrightTestCase):
    STUB_GLOBAL_SSE = False

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.url = f"{self.live_server_url}/files"
        # Every stream request, in order, left unanswered until a test answers it.
        self.streams = []
        self.snapshot_events = []
        self.context.route(STREAM, self._hold_stream)
        self.context.route(SNAPSHOT, self._serve_snapshot)

    def _hold_stream(self, route):
        self.streams.append(route)

    def _serve_snapshot(self, route):
        route.fulfill(json={"events": self.snapshot_events})

    def _stream(self, number, watcher):
        """The route of the number-th stream request, once it has been made.

        ``watcher`` is any page still open: waiting on it is what lets
        Playwright deliver the request.
        """
        for _ in range(STREAM_TIMEOUT_MS // POLL_MS):
            if len(self.streams) >= number:
                return self.streams[number - 1]
            watcher.wait_for_timeout(POLL_MS)
        self.fail(f"{len(self.streams)} stream request(s), expected {number}")

    def _answer(self, route, *events):
        route.fulfill(
            status=200, content_type="text/event-stream", body=QUIET + "".join(events)
        )

    def _stream_owners(self):
        return [route.request.frame.page for route in self.streams]

    def _open_leader(self):
        self.page.goto(self.url)
        self._stream(1, self.page)
        return self.page

    def _open_follower(self):
        page = self.context.new_page()
        with page.expect_response(SNAPSHOT):
            page.goto(self.url)
        return page

    def test_a_second_tab_shares_the_stream_of_the_first(self):
        leader = self._open_leader()
        self.snapshot_events = [{"event": "notifications.count", "data": {"unread": 4}}]
        follower = self._open_follower()

        follower.wait_for_function(f"() => {UNREAD} === 4")

        self._answer(
            self._stream(1, leader),
            _event("notifications.count", {"unread": 7}, event_id="cursor-1"),
        )

        leader.wait_for_function(f"() => {UNREAD} === 7")
        follower.wait_for_function(f"() => {UNREAD} === 7")
        self.assertEqual(self._stream_owners(), [leader])

    def test_a_follower_takes_the_stream_over_where_the_closed_leader_stopped(self):
        leader = self._open_leader()
        follower = self._open_follower()
        self._answer(
            self._stream(1, leader),
            _event("notifications.count", {"unread": 7}, event_id="cursor-1"),
        )
        follower.wait_for_function(f"() => {UNREAD} === 7")
        follower.evaluate(COUNT_RECONNECTS)

        leader.close()

        taken = self._stream(2, follower)
        self.assertIs(taken.request.frame.page, follower)
        self.assertIn("last_event_id=cursor-1", taken.request.url)
        # Events without an id were lost between the two streams.
        self._answer(taken)
        follower.wait_for_function("() => window.__reconnects === 1")

    def test_a_page_set_aside_gives_the_stream_up_and_follows_when_it_is_back(self):
        leader = self._open_leader()
        follower = self._open_follower()

        leader.evaluate(SET_ASIDE)
        taken = self._stream(2, follower)
        self.assertIs(taken.request.frame.page, follower)

        self.snapshot_events = [{"event": "notifications.count", "data": {"unread": 4}}]
        leader.evaluate(COUNT_RECONNECTS)
        with leader.expect_response(SNAPSHOT):
            leader.evaluate(BROUGHT_BACK)

        leader.wait_for_function(f"() => {UNREAD} === 4")
        self.assertEqual(leader.evaluate("() => window.__reconnects"), 1)

        self._answer(taken, _event("notifications.count", {"unread": 9}))
        leader.wait_for_function(f"() => {UNREAD} === 9")
        self.assertEqual(self._stream_owners(), [leader, follower])

    def test_a_tab_takes_the_stream_from_a_leader_that_stopped_answering(self):
        suspended = self.page
        suspended.goto(f"{self.live_server_url}/offline")
        suspended.evaluate(HOLD_LOCK_SILENTLY, f"workspace-sse:{self.user.pk}")

        tab = self.context.new_page()
        tab.goto(self.url)

        taken = self._stream(1, tab)
        self.assertIs(taken.request.frame.page, tab)
