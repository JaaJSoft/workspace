"""The calendar's subscription link, managed from the Edit dialog.

Drives the dialog in a browser, then fetches each link the way a calendar app
would: from a separate HTTP client with no session, since the token in the URL
is the only credential such an app has.
"""

from __future__ import annotations

from datetime import UTC, datetime

from playwright.sync_api import expect

from workspace.calendar.models import Calendar, Event
from workspace.common.tests.e2e.base import PlaywrightTestCase


class CalendarFeedLinkTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="feeder")
        self.calendar = Calendar.objects.create(name="Team", owner=self.user)
        Event.objects.create(
            calendar=self.calendar,
            owner=self.user,
            title="Planning",
            start=datetime(2026, 10, 5, 9, tzinfo=UTC),
            end=datetime(2026, 10, 5, 10, tzinfo=UTC),
        )
        self.login_as(self.user)
        # A calendar app: no cookie, nothing but the URL.
        self.app = self._playwright.request.new_context()

    def tearDown(self):
        self.app.dispose()
        super().tearDown()

    def open_edit_dialog(self):
        self.page.goto(f"{self.live_server_url}/calendar")
        row = self.page.locator("aside").get_by_text("Team", exact=True)
        row.hover()
        self.page.locator("aside").get_by_title("Edit").first.click()
        dialog = self.page.locator("dialog.modal-open")
        expect(dialog.get_by_text("Subscription link")).to_be_visible()
        return dialog

    def link(self, dialog):
        field = dialog.get_by_label("Subscription link")
        expect(field).to_be_visible()
        return field.input_value()

    def confirm(self, label):
        self.page.locator("#app-dialog-confirm-ok", has_text=label).click()

    def test_create_replace_and_turn_off_the_link(self):
        dialog = self.open_edit_dialog()

        dialog.get_by_role("button", name="Create link").click()
        first = self.link(dialog)
        self.assertIn("/calendar/feeds/", first)

        response = self.app.get(first)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/calendar"))
        self.assertIn("SUMMARY:Planning", response.text())

        dialog.get_by_role("button", name="New link").click()
        self.confirm("Replace")
        expect(dialog.get_by_label("Subscription link")).not_to_have_value(first)
        second = self.link(dialog)
        self.assertEqual(self.app.get(first).status, 404)
        self.assertEqual(self.app.get(second).status, 200)

        dialog.get_by_role("button", name="Turn off").click()
        self.confirm("Turn off")
        expect(dialog.get_by_role("button", name="Create link")).to_be_visible()
        self.assertEqual(self.app.get(second).status, 404)

        self.calendar.refresh_from_db()
        self.assertIsNone(self.calendar.feed_token)

    def test_link_survives_reopening_the_dialog(self):
        dialog = self.open_edit_dialog()
        dialog.get_by_role("button", name="Create link").click()
        url = self.link(dialog)

        dialog = self.open_edit_dialog()

        self.assertEqual(self.link(dialog), url)
