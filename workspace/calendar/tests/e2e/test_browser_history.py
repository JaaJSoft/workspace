"""E2E: the event panel, the view and the date follow browser back and forward.

Opening an event adds a history entry, so back closes it and forward reopens
it; closing it steps back onto the entry it was opened from rather than
stacking a second copy of the calendar. A back or forward only replays an
entry: it never writes one, which would cut the forward history or rewrite
the address of the entry it lands on.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from playwright.sync_api import expect

from workspace.calendar.models import Calendar, Event, Poll
from workspace.common.tests.e2e.base import PlaywrightTestCase

BASE_QUERY = "?view=month&date=2026-03-15"


class CalendarBrowserHistoryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="historian")
        calendar = Calendar.objects.create(name="Work", owner=self.user)
        self.event = Event.objects.create(
            calendar=calendar,
            title="Standup",
            start=datetime(2026, 3, 18, 10, tzinfo=UTC),
            end=datetime(2026, 3, 18, 11, tzinfo=UTC),
            owner=self.user,
        )
        self.login_as(self.user)
        self.base_url = f"{self.live_server_url}/calendar{BASE_QUERY}"
        self.event_url = f"{self.base_url}&event={self.event.uuid}"

    # ---- helpers ---------------------------------------------------------

    def _panel_title(self):
        return self.page.locator('h3[x-text="form.title"]')

    def _open_calendar(self, url=None):
        self.page.goto(url or self.base_url)
        expect(self.page.locator(".fc-event", has_text="Standup")).to_be_visible()

    def _open_event(self):
        self.page.locator(".fc-event", has_text="Standup").click()
        expect(self._panel_title()).to_have_text("Standup")
        expect(self.page).to_have_url(self.event_url)

    def _close_panel(self):
        self.page.get_by_role("main").get_by_label("Close", exact=True).click()
        expect(self._panel_title()).to_be_hidden()

    def _nav(self, expression):
        return self.page.evaluate(f"window.navHistory.{expression}")

    def _shown(self):
        """The view and the day the calendar is showing."""
        return self.page.evaluate(
            "(app => [app.currentView, app._dayKey(app.calendar.getDate())])"
            "(Alpine.$data(document.querySelector('[x-ref=calendarEl]')))"
        )

    # ---- tests -----------------------------------------------------------

    def test_closing_an_event_steps_back_and_forward_reopens_it(self):
        self._open_calendar()
        self._open_event()

        self._close_panel()

        expect(self.page).to_have_url(self.base_url)
        self.assertTrue(
            self._nav("canGoForward()"),
            "closing stacked a new entry instead of stepping back over the opening",
        )
        self.page.go_forward()
        expect(self.page).to_have_url(self.event_url)
        expect(self._panel_title()).to_have_text("Standup")

    def test_the_mobile_back_arrow_closes_like_the_desktop_button(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self._open_calendar()
        self._open_event()

        self.page.get_by_label("Back to calendar").click()

        expect(self._panel_title()).to_be_hidden()
        expect(self.page).to_have_url(self.base_url)
        self.assertTrue(self._nav("canGoForward()"))

    def test_back_then_forward_onto_an_event_keeps_the_forward_history(self):
        self._open_calendar()
        self._open_event()

        self.page.go_back()
        expect(self.page).to_have_url(self.base_url)
        expect(self._panel_title()).to_be_hidden()

        self.page.go_forward()
        expect(self.page).to_have_url(self.event_url)
        expect(self._panel_title()).to_have_text("Standup")

        # Reopening on forward must not push: the entry is already there.
        self.page.go_back()
        expect(self.page).to_have_url(self.base_url)
        expect(self._panel_title()).to_be_hidden()
        self.assertTrue(self._nav("canGoForward()"))

    def test_closing_a_deep_linked_event_rewrites_the_entry(self):
        # A copied event link: the calendar jumps to the event's day.
        self._open_calendar(f"{self.live_server_url}/calendar?event={self.event.uuid}")
        expect(self._panel_title()).to_have_text("Standup")

        self._close_panel()

        expect(self.page).to_have_url(
            f"{self.live_server_url}/calendar?view=month&date=2026-03-18"
        )
        self.assertFalse(
            self._nav("canGoBack()"),
            "landing on ?event= pushed a duplicate entry behind the calendar",
        )

    def test_back_restores_the_entry_after_view_and_date_changes(self):
        self._open_calendar()
        self._open_event()
        self.page.keyboard.press("w")
        self.page.keyboard.press("ArrowRight")
        expect(self.page).to_have_url(
            f"{self.live_server_url}/calendar?view=week&date=2026-03-16"
            f"&event={self.event.uuid}"
        )

        self.page.go_back()

        expect(self._panel_title()).to_be_hidden()
        self.assertEqual(self._shown(), ["dayGridMonth", "2026-03-15"])
        # The restore must not rewrite the entry it landed on.
        self.page.wait_for_timeout(300)
        expect(self.page).to_have_url(self.base_url)

    def test_the_poll_modal_follows_back_and_forward(self):
        poll = Poll.objects.create(title="Team lunch", created_by=self.user)
        self._open_calendar()
        self._open_event()
        self.page.get_by_role("button", name="Polls", exact=True).click()
        self.page.get_by_text("Team lunch").click()
        expect(self.page).to_have_url(re.compile(f"poll={poll.uuid}"))
        poll_modal = self.page.locator("dialog.modal-open")
        expect(poll_modal).to_have_count(1)

        self.page.go_back()

        expect(self.page).to_have_url(self.base_url)
        expect(poll_modal).to_have_count(0)
        expect(self._panel_title()).to_be_hidden()

        self.page.go_forward()

        expect(self.page).to_have_url(re.compile(f"poll={poll.uuid}"))
        expect(poll_modal).to_have_count(1)
        expect(self._panel_title()).to_have_text("Standup")

    def test_back_onto_an_entry_from_an_earlier_page_load_does_not_reload(self):
        self._open_calendar()
        self._open_event()
        self.page.reload()
        expect(self._panel_title()).to_have_text("Standup")
        self.page.evaluate("window.__sameDocument = true")

        self.page.go_back()

        expect(self.page).to_have_url(self.base_url)
        expect(self._panel_title()).to_be_hidden()
        self.assertTrue(self.page.evaluate("window.__sameDocument === true"))
