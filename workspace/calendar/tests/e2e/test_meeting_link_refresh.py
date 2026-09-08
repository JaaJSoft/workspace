"""E2E: creating a meeting link updates the card behind the grid too.

The panel is opened from FullCalendar's cached copy of the event, so an
action that changes the event on the server and only patches the open panel
leaves that copy behind: closing the panel and clicking the same event
reopens the pre-meeting state, "Meeting link" button included, and clicking
it again posts a second creation. Only a grid refetch settles it, and only
a real browser can tell - the cache lives in FullCalendar, not in our state.
"""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from playwright.sync_api import expect

from workspace.calendar.models import Calendar, Event
from workspace.common.tests.e2e.base import PlaywrightTestCase


class MeetingLinkRefreshTests(PlaywrightTestCase):
    def test_reopening_the_event_shows_the_room_button(self):
        owner = self.create_user(username="meetingowner")
        calendar = Calendar.objects.create(owner=owner, name="Work")
        start = timezone.now().replace(microsecond=0) + timedelta(hours=2)
        event = Event.objects.create(
            calendar=calendar,
            owner=owner,
            title="Product sync",
            start=start,
            end=start + timedelta(hours=1),
        )
        self.login_as(owner)

        self.page.goto(f"{self.live_server_url}/calendar?event={event.uuid}")
        create = self.page.get_by_role("button", name="Meeting link")
        expect(create).to_be_visible()

        create.click()
        join = self.page.get_by_role("link", name="Join the meeting")
        expect(join).to_be_visible()

        # Back to the grid, then in again through the cached event.
        self.page.get_by_role("main").get_by_label("Close", exact=True).click()
        expect(join).to_be_hidden()
        self.page.locator(".fc-event", has_text="Product sync").first.click()

        expect(self.page.get_by_role("link", name="Join the meeting")).to_be_visible()
        expect(self.page.get_by_role("button", name="Meeting link")).to_have_count(0)
