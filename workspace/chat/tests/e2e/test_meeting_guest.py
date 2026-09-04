"""A visitor with no account knocks, is admitted by the host, and lands in
the room. Skipped unless E2E=1 is set.

Process boundary this suite has to respect: without REDIS_URL the guest
mailbox and the host wake-ups live in Django's per-process LocMemCache, and
the live server runs in a thread of the test process. ORM reads from the test
body are therefore fine, but anything a stream must notice - knocking,
admitting, starting the call - goes through the live server, either through
the browser or through an authenticated ``APIRequestContext``, never through
a service call in the test body.
"""

from __future__ import annotations

import threading
import time

from django.utils import timezone
from playwright.sync_api import expect

from workspace.chat.models import MeetingGuest
from workspace.chat.services.meetings import create_meeting
from workspace.chat.tests.meeting_fixtures import make_event
from workspace.common.tests.e2e.base import PlaywrightTestCase

# Cross-context expectations wait on a real SSE hop: the guest's stream, the
# host's stream, a poll interval on each side.
CROSS_CONTEXT_TIMEOUT_MS = 15_000


def _tile(grid, display_name):
    """One remote participant's tile. Addressed by its accessible name: the
    display name itself renders twice inside a tile (avatar caption and video
    overlay), which strict mode refuses."""
    return grid.get_by_role("button", name=f"Spotlight {display_name}")


class MeetingGuestTests(PlaywrightTestCase):
    # Both streams here are the real ones: the host's /api/v1/stream carries
    # the guest's join and their message, and stubbing it would leave the
    # host page blind to everything this suite asserts on.
    STUB_GLOBAL_SSE = False

    @classmethod
    def setUpClass(cls):
        # Re-launch Chromium with fake media so getUserMedia resolves headless
        # on both sides of the call. Same shape as test_voice_room; base.py is
        # shared by every module and must not grow these flags.
        super().setUpClass()
        cls.browser.close()
        browser_type = getattr(cls._playwright, cls.BROWSER_NAME)
        cls.browser = browser_type.launch(
            headless=cls.HEADLESS,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
            ],
        )

    def setUp(self):
        super().setUp()
        # Registered first so it runs last: an SSE worker still holding its
        # SQLite connection when the runner deletes test_db.sqlite3 fails the
        # whole run on Windows with WinError 32.
        self.addCleanup(self._wait_for_server_threads)
        self.context.grant_permissions(["microphone"])

        self.host = self.create_user(username="meet-host", password="pass12345")
        event = make_event(
            self.host, start=timezone.now() + timezone.timedelta(minutes=5)
        )
        self.meeting = create_meeting(event, self.host)
        self.login_as(self.host)

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()
        super().tearDown()

    def _wait_for_server_threads(self, timeout=30.0):
        """Let the live server's in-flight streams notice the closed sockets.

        Both stream views only observe the disconnect on their next write (a
        keepalive, at most 15s away), and each holds its own SQLite connection
        until then.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            busy = [
                t
                for t in threading.enumerate()
                if "process_request_thread" in t.name and t.is_alive()
            ]
            if not busy:
                return
            time.sleep(0.25)

    # ---- guest driving ---------------------------------------------------

    def _open_guest_page(self):
        guest_ctx = self.browser.new_context()
        self.addCleanup(guest_ctx.close)
        guest_ctx.grant_permissions(["microphone"])
        guest = guest_ctx.new_page()
        guest.goto(f"{self.live_server_url}/meet/{self.meeting.slug}")
        return guest

    def _knock(self, guest, name):
        guest.get_by_label("Your name").fill(name)
        guest.get_by_role("button", name="Ask to join").click()
        expect(guest.get_by_text("Waiting for the host")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
        return MeetingGuest.objects.get(meeting=self.meeting, display_name=name)

    def _admit_over_http(self, guest_row):
        """Admit through the live server, with the host's own session.

        The host cannot admit from the UI before a call exists (the lobby
        panel lives on the call stage), and a service call from the test body
        would not be the server acting.
        """
        # A plain HTTP fetch, never a page load: the host must not open a
        # second SSE connection. Without Redis the mailbox is a LocMemCache
        # queue, so a stream the browser has navigated away from keeps
        # draining it - for up to a keepalive - and eats the events the new
        # connection is waiting for.
        self.context.request.get(f"{self.live_server_url}/login")
        csrf = next(
            c["value"] for c in self.context.cookies() if c["name"] == "csrftoken"
        )
        resp = self.context.request.post(
            f"{self.live_server_url}/api/v1/chat/meetings/"
            f"{self.meeting.uuid}/guests/{guest_row.uuid}/admit",
            headers={"X-CSRFToken": csrf, "Content-Type": "application/json"},
            data="{}",
        )
        self.assertTrue(resp.ok, f"{resp.status}: {resp.text()}")

    def _open_host_room(self):
        self.page.goto(
            f"{self.live_server_url}/chat/room/{self.meeting.conversation_id}"
        )
        grid = self.page.locator('[data-testid="participants-grid"]')
        expect(grid).to_be_visible(timeout=CROSS_CONTEXT_TIMEOUT_MS)
        expect(grid.locator('[data-testid="self-tile"]')).to_contain_text(
            "meet-host", timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
        return grid

    # ---- the flows -------------------------------------------------------

    def test_guest_knocks_is_admitted_and_joins_a_running_call(self):
        host_grid = self._open_host_room()

        guest = self._open_guest_page()
        self._knock(guest, "Visitor")

        # The knock reaches the host's lobby, and admitting is a click.
        self.page.get_by_title("Lobby").click()
        lobby = self.page.locator('[data-testid="lobby-panel"]')
        expect(lobby.get_by_text("Visitor")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
        lobby.get_by_role("button", name="Admit").click()

        # The call is already running, so admission alone puts the guest in it.
        guest_grid = guest.locator('[data-testid="participants-grid"]')
        expect(guest_grid).to_be_visible(timeout=CROSS_CONTEXT_TIMEOUT_MS)
        expect(_tile(guest_grid, "meet-host")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
        expect(_tile(host_grid, "Visitor")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )

        # The guest's message lands in the member timeline, marked as a guest's.
        guest.get_by_placeholder("Message").fill("hello from outside")
        guest.get_by_role("button", name="Send").click()
        messages = self.page.locator("#messages-container")
        expect(messages.get_by_text("hello from outside")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
        expect(messages.locator("chat-message-group[guest]")).to_have_count(
            1, timeout=CROSS_CONTEXT_TIMEOUT_MS
        )

        # Removing the guest closes their page, whatever they were doing.
        self.page.get_by_title("Remove guest").click()
        expect(guest.get_by_text("You were removed from the meeting")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )

    def test_a_guest_admitted_before_the_call_joins_when_it_starts(self):
        guest = self._open_guest_page()
        guest_row = self._knock(guest, "Early")
        self._admit_over_http(guest_row)

        # Admitted with nothing to join yet: the room shows the waiting card.
        expect(
            guest.get_by_text("Waiting for the host to start the call")
        ).to_be_visible(timeout=CROSS_CONTEXT_TIMEOUT_MS)
        expect(guest.get_by_role("button", name="Leave the meeting")).to_be_visible()

        # The host opening the room starts the call, and call_started is the
        # only thing that tells a guest sitting in an empty room to join.
        host_grid = self._open_host_room()
        guest_grid = guest.locator('[data-testid="participants-grid"]')
        expect(guest_grid).to_be_visible(timeout=CROSS_CONTEXT_TIMEOUT_MS)
        expect(_tile(guest_grid, "meet-host")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
        expect(_tile(host_grid, "Early")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
