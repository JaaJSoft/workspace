"""E2E: a stream reconnection refreshes the agenda where the reader is.

The stream script dispatches ``sse:reconnect`` each time the event stream
opens again, at least every half hour. The agenda must reload in place: a
reader a few weeks ahead keeps their rows and their scroll position instead
of being sent back to today. Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from workspace.calendar.models import Calendar, Event
from workspace.common.tests.e2e.base import PlaywrightTestCase

# AGENDA_PAGE_SIZE in calendar_events.js; three pages and a partial one.
PAGE_SIZE = 20
SEEDED = 70
LOADED = 3 * PAGE_SIZE

ROWS = "button.agenda-event"
SCROLLER = "document.querySelector('.agenda-view')"


class AgendaReconnectTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="agenda-reader")
        calendar = Calendar.objects.create(name="Work", owner=self.user)
        start = timezone.now().replace(microsecond=0) + timedelta(hours=1)
        Event.objects.bulk_create(
            Event(
                calendar=calendar,
                owner=self.user,
                title=f"Event {i:02d}",
                start=start + timedelta(days=i),
                end=start + timedelta(days=i, hours=1),
            )
            for i in range(SEEDED)
        )

    def _rendered_titles(self):
        return self.page.eval_on_selector_all(
            f"{ROWS} .agenda-event-title-text", "els => els.map(el => el.textContent)"
        )

    def _scroll_until(self, count):
        # The observer only reports the sentinel entering the viewport, so
        # each page needs it out of view first and then back in.
        rows = self.page.locator(ROWS)
        while rows.count() < count:
            loaded = rows.count()
            self.page.evaluate(
                f"""async () => {{
                  const el = {SCROLLER};
                  el.scrollTop = 0;
                  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
                  el.scrollTop = el.scrollHeight;
                }}"""
            )
            self.page.wait_for_function(
                f"document.querySelectorAll('{ROWS}').length > {loaded}"
            )

    def test_a_stream_reconnection_refreshes_the_agenda_in_place(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/calendar?view=agenda")
        rows = self.page.locator(ROWS)
        expect(rows).to_have_count(PAGE_SIZE)
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

        self._scroll_until(LOADED)
        expect(rows).to_have_count(LOADED)
        titles = self._rendered_titles()

        # Somewhere in the middle of the loaded pages, as a reader would be.
        self.page.evaluate(f"{SCROLLER}.scrollTop = 1800")
        before = self.page.evaluate(f"{SCROLLER}.scrollTop")
        self.assertGreater(before, 0)

        # The fewest rows any frame painted while the agenda refreshed.
        self.page.evaluate(
            f"""() => {{
              window.__fewestRows = Infinity;
              const sample = () => {{
                window.__fewestRows = Math.min(
                  window.__fewestRows, document.querySelectorAll('{ROWS}').length
                );
                requestAnimationFrame(sample);
              }};
              sample();
            }}"""
        )
        # What the stream script dispatches each time the stream reopens.
        with self.page.expect_response(
            lambda r: "/api/v1/events?" in r.url and "after=" in r.url
        ):
            self.page.evaluate("window.dispatchEvent(new CustomEvent('sse:reconnect'))")
        # Two frames: one for Alpine to render the swap, one to sample it.
        self.page.evaluate(
            "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
        )

        self.assertEqual(
            self.page.evaluate("window.__fewestRows"),
            LOADED,
            "the agenda went through a shorter or empty state",
        )
        self.assertEqual(self._rendered_titles(), titles)
        self.assertEqual(
            self.page.evaluate(f"{SCROLLER}.scrollTop"),
            before,
            "the refresh moved the reader away from where they were",
        )
