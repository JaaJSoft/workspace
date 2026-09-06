"""A visitor with no account knocks, is admitted by the host, and lands in
the room. Skipped unless E2E=1 is set.

Process boundary this suite has to respect: without REDIS_URL the guest
mailbox and the host wake-ups live in Django's per-process LocMemCache, and
the live server runs in a thread of the test process. ORM reads from the test
body are therefore fine, but anything a stream must notice - knocking,
admitting, starting the call - goes through the live server, either through
the browser or through an authenticated ``APIRequestContext``, never through
a service call in the test body.

The pane the guest chats in is the member pane: the same server-rendered
partial, the same composer, the same message shell. Every assertion below
therefore reads what the member side reads - <chat-message-group> elements
and their attributes - rather than anything the guest page renders on its
own, because it renders nothing on its own.
"""

from __future__ import annotations

import threading
import time

from django.utils import timezone
from playwright.sync_api import expect

from workspace.chat.models import MeetingGuest, Message
from workspace.chat.services.meetings import create_meeting
from workspace.chat.tests.meeting_fixtures import make_event
from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.users.services.settings import set_setting

# Cross-context expectations wait on a real SSE hop: the guest's stream, the
# host's stream, a poll interval on each side.
CROSS_CONTEXT_TIMEOUT_MS = 15_000


def _tile(grid, display_name):
    """One remote participant's tile. Addressed by its accessible name: the
    display name itself renders twice inside a tile (avatar caption and video
    overlay), which strict mode refuses."""
    return grid.get_by_role("button", name=f"Spotlight {display_name}")


def _composer(page):
    """The shared composer's desktop textarea.

    Both inputs the partial renders carry the same mixin state; below sm the
    compact one takes over, and the default viewport is wide enough that the
    desktop one is the visible half on every page this suite drives.
    """
    return page.locator('textarea[placeholder="Type a message..."]')


def _send(page, body):
    """Type into the shared composer and click its send button.

    The button is addressed by its accessible name: it is an icon button, and
    the compact input renders a second copy of it that CSS keeps out of the
    accessibility tree at this viewport.
    """
    _composer(page).fill(body)
    page.get_by_role("button", name="Send message").click()


def _group_for(root, message_uuid):
    """The <chat-message-group> holding one server-rendered message."""
    return root.locator(f'chat-message-group:has([data-message-uuid="{message_uuid}"])')


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
        # Already under way, not about to start: a guest's message window is
        # floored at their occurrence's start, so a meeting still in its
        # early-join lead would show them nothing anybody says before it.
        event = make_event(
            self.host, start=timezone.now() - timezone.timedelta(minutes=5)
        )
        self.meeting = create_meeting(event, self.host)
        # A member's reply is a thread reply, filed into the thread panel
        # unless they asked for replies in the flow. A guest has no threads
        # at all, so their copy is always in the flow; this puts the two
        # panes on the same footing, which is what the parity below reads.
        set_setting(self.host, "chat", "preferences", {"showThreadRepliesInline": True})
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

    def _message(self, pane, body):
        """The row the server stored for a body typed in a composer.

        Waits for the given pane to hold a bubble the server rendered for it
        first: data-message-uuid only exists on the server's render, so seeing
        one is what makes the ORM read below race-free. The uuid it returns is
        how both panes address the message.
        """
        expect(pane.locator("[data-message-uuid]").filter(has_text=body)).to_have_count(
            1, timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
        return Message.objects.get(
            conversation_id=self.meeting.conversation_id, body=body
        )

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

        # The lobby is a modal dialog: it stays open (there could be another
        # guest to admit) but blocks the rest of the page until closed. daisyUI
        # fades a closed `.modal` out with opacity rather than display:none, so
        # it still counts as "visible" by Playwright's own definition - the
        # native <dialog>.open property is what actually reflects the state.
        lobby.locator(".modal-box").get_by_role("button", name="Close").click()
        self.page.wait_for_function(
            "() => !document.querySelector('[data-testid=\"lobby-panel\"]').open"
        )

        # The call is already running, so admission alone puts the guest in it.
        guest_grid = guest.locator('[data-testid="participants-grid"]')
        expect(guest_grid).to_be_visible(timeout=CROSS_CONTEXT_TIMEOUT_MS)
        expect(_tile(guest_grid, "meet-host")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
        expect(_tile(host_grid, "Visitor")).to_be_visible(
            timeout=CROSS_CONTEXT_TIMEOUT_MS
        )

        host_list = self.page.locator("#messages-container")
        guest_list = guest.locator("#message-list")

        # The host speaks first, and it reaches the guest as a server-rendered
        # group - not a bubble the guest page drew for itself.
        _send(self.page, "welcome to the meeting")
        host_message = self._message(guest_list, "welcome to the meeting")
        expect(_group_for(guest_list, host_message.uuid)).to_have_count(1)
        expect(guest_list.get_by_text("welcome to the meeting")).to_be_visible()
        # Somebody else's message: neither the viewer's own nor a guest's.
        expect(guest_list.locator("chat-message-group[own]")).to_have_count(0)

        # The guest answers through the same composer, and lands in the member
        # timeline marked as a guest's.
        _send(guest, "hello from outside")
        guest_message = self._message(host_list, "hello from outside")
        expect(_group_for(host_list, guest_message.uuid)).to_have_attribute("guest", "")
        expect(host_list.locator("chat-message-group[guest]")).to_have_count(1)

        # The same row, re-rendered for its author, is the one group the guest
        # owns.
        expect(_group_for(guest_list, guest_message.uuid)).to_have_attribute(
            "own", "", timeout=CROSS_CONTEXT_TIMEOUT_MS
        )
        expect(guest_list.locator("chat-message-group[own]")).to_have_count(1)

        # Replying is a hover away, exactly as it is for a member: the toolbar
        # under the bubble is the shared template's, and the banner it raises
        # is the shared composer's.
        host_bubble = guest_list.locator(f'[data-message-uuid="{host_message.uuid}"]')
        host_bubble.hover()
        # The toolbar is a sibling of the bubble: the shell lifts every
        # data-part="after-bubble" child out into the hover wrapper.
        host_bubble.locator("xpath=..").get_by_title("Reply").click()
        expect(guest.get_by_text("Replying to meet-host")).to_be_visible()
        _send(guest, "answering the host")

        # The quote is server-built from data-reply-uuid on both sides, and
        # the shell turns it into a link to the message it quotes.
        reply = self._message(guest_list, "answering the host")
        for pane in (guest_list, host_list):
            quoted = pane.locator(
                f'[data-message-uuid="{reply.uuid}"]'
                f'[data-reply-uuid="{host_message.uuid}"]'
            )
            expect(quoted).to_have_count(1, timeout=CROSS_CONTEXT_TIMEOUT_MS)
            expect(
                quoted.locator(f'a[href="#msg-{host_message.uuid}"]')
            ).to_contain_text("welcome to the meeting")

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
