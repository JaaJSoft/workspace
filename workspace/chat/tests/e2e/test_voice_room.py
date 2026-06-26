"""E2E safety-net: the voice call survives main-app navigation.

The voice room opens in its own browser tab and owns the WebRTC call.
Navigating the main app (a full page reload to /files) must NOT kill the
call. Under the old architecture the call lived in the chat page and would
have died on that navigation.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember
from workspace.common.tests.e2e.base import PlaywrightTestCase


class VoiceRoomNavigationTests(PlaywrightTestCase):
    """Core regression guard: the voice call survives main-tab navigation."""

    @classmethod
    def setUpClass(cls):
        # Start Playwright and the live server via the base class, then
        # re-launch Chromium with fake-media device flags so
        # navigator.mediaDevices.getUserMedia works headless without a real mic.
        # The base setUpClass launches the browser without these flags; close it
        # and open a new one. Do NOT modify base.py - it affects every module.
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
        # Grant microphone permission so getUserMedia resolves without a dialog.
        self.context.grant_permissions(["microphone"])

        self.user = self.create_user(username="voice-tester", password="pass12345")
        self.peer = self.create_user(username="voice-peer")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()
        super().tearDown()

    def test_call_survives_main_app_navigation(self):
        # Step 1: Log in, navigate to /chat, open the DM conversation.
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")

        list_root = self.page.locator("#conversation-list")
        expect(list_root.get_by_text("voice-peer")).to_be_visible()
        list_root.get_by_text("voice-peer").click()

        # The conversation pane becomes active once the composer is visible.
        composer = self.page.locator('textarea[placeholder="Type a message..."]')
        expect(composer).to_be_visible()

        # Step 2: Click the phone button. openCallRoom() calls window.open(),
        # which opens the room in a new tab. Capture the new page with
        # expect_page before the click fires.
        with self.context.expect_page() as room_page_info:
            self.page.get_by_title("Start or join a call").click()

        room_page = room_page_info.value

        # Attach console/error capture before waiting so load-time errors are
        # captured and surfaced in the failure message.
        _room_console: list[str] = []
        room_page.on(
            "console",
            lambda msg: _room_console.append(f"{msg.type}: {msg.text}"),
        )
        room_page.on(
            "pageerror",
            lambda exc: _room_console.append(f"pageerror: {exc}"),
        )

        room_page.wait_for_load_state("domcontentloaded")

        # Step 3: The room's init() calls startOrJoinCall(), which:
        #   - calls navigator.mediaDevices.getUserMedia (fake device, no real mic)
        #   - POSTs to /api/v1/chat/conversations/<uuid>/call/join
        #   - populates callParticipants from the server response (includes the user)
        # The x-for loop renders one tile per participant. Wait for the current
        # user's display_name ("voice-tester") to appear in the participants section.
        participants_section = room_page.locator('[data-testid="participants-grid"]')
        try:
            expect(participants_section.get_by_text("voice-tester")).to_be_visible(
                timeout=15_000
            )
        except Exception:
            print("\n[e2e:room] join failed - console messages from room page:")
            for line in _room_console:
                print(f"[e2e:room]   {line}")
            raise

        # Step 3b: Verify the conversation header shows the real name, not
        # "Group". activeConversation is now seeded from room-conversation-data
        # (the full ConversationListSerializer payload), so conversationName()
        # returns the other member's username for a DM, not the "Group" fallback.
        conv_header = room_page.locator("h3.font-semibold.text-sm.truncate").first
        try:
            expect(conv_header).not_to_have_text("Group", timeout=5_000)
            expect(conv_header).to_have_text("voice-peer", timeout=5_000)
        except Exception:
            print("\n[e2e:room] header check failed - console messages from room page:")
            for line in _room_console:
                print(f"[e2e:room]   {line}")
            raise

        # Step 4: Type a message into the room's composer and press Enter.
        # This proves that autoResize($el), handleInputKeydown($event), and
        # getMessageInput() all resolve correctly in chatRoomApp (the bug was
        # that these helpers lived only in chatApp's factory body and were absent
        # from chatRoomApp, causing a ReferenceError/TypeError per keystroke and
        # Enter-to-send being dead).
        # The room uses the same conversation_pane.html partial; the desktop
        # textarea has x-ref="messageInput" and placeholder "Type a message...".
        room_composer = room_page.locator('textarea[placeholder="Type a message..."]')
        try:
            expect(room_composer).to_be_visible(timeout=5_000)
        except Exception:
            print(
                "\n[e2e:room] composer not visible - console messages from room page:"
            )
            for line in _room_console:
                print(f"[e2e:room]   {line}")
            raise

        room_test_message = "hello from the voice room composer test"
        room_composer.fill(room_test_message)
        room_composer.press("Enter")

        room_messages = room_page.locator("#messages-container")
        try:
            expect(room_messages.get_by_text(room_test_message)).to_be_visible(
                timeout=10_000
            )
        except Exception:
            print(
                "\n[e2e:room] message not visible after Enter - console messages from room page:"
            )
            for line in _room_console:
                print(f"[e2e:room]   {line}")
            raise

        # Step 5: Navigate the MAIN tab to /files - a full page reload to another
        # module. Under the old architecture the call lived in the chat page and
        # would have been destroyed when the Alpine app tore down here.
        self.page.goto(f"{self.live_server_url}/files")
        self.page.wait_for_load_state("domcontentloaded")

        # Step 6: The room tab must still be alive and still show the participant
        # tile. This is the core regression assertion: because the call is owned
        # by the room tab (not the main tab), main-tab navigation cannot kill it.
        expect(participants_section.get_by_text("voice-tester")).to_be_visible(
            timeout=5_000
        )

    def test_info_panel_overlays_full_chat_pane_in_room(self):
        """Info panel must REPLACE the chat in the room (overlay), not sit beside it.

        The voice room uses overlay_panels=True so the info panel fills the full
        chat aside width rather than squeezing a 288px side-by-side panel next to
        the messages. This geometry assertion proves the fix is in place.
        """
        # Open the main chat page and select the DM conversation.
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")

        list_root = self.page.locator("#conversation-list")
        expect(list_root.get_by_text("voice-peer")).to_be_visible()
        list_root.get_by_text("voice-peer").click()

        composer = self.page.locator('textarea[placeholder="Type a message..."]')
        expect(composer).to_be_visible()

        # Open the voice room tab.
        with self.context.expect_page() as room_page_info:
            self.page.get_by_title("Start or join a call").click()

        room_page = room_page_info.value

        _room_console: list[str] = []
        room_page.on(
            "console",
            lambda msg: _room_console.append(f"{msg.type}: {msg.text}"),
        )
        room_page.on(
            "pageerror",
            lambda exc: _room_console.append(f"pageerror: {exc}"),
        )

        room_page.wait_for_load_state("domcontentloaded")

        # Wait for the room to be ready before checking panel geometry.
        participants_section = room_page.locator('[data-testid="participants-grid"]')
        try:
            expect(participants_section.get_by_text("voice-tester")).to_be_visible(
                timeout=15_000
            )
        except Exception:
            print("\n[e2e:room] join failed - console messages from room page:")
            for line in _room_console:
                print(f"[e2e:room]   {line}")
            raise

        # Measure the chat aside before opening the info panel.
        chat_aside = room_page.locator("aside")
        aside_box = chat_aside.bounding_box()
        assert aside_box is not None, "chat aside element not found"

        # The overlay info panel must not be visible before the button is clicked.
        info_overlay = room_page.locator('[data-testid="info-panel-overlay"]')
        expect(info_overlay).not_to_be_visible()

        # Click the info button ("I").
        try:
            room_page.get_by_title("Conversation info (Alt+I)").click()
        except Exception:
            print(
                "\n[e2e:room] info button not found - console messages from room page:"
            )
            for line in _room_console:
                print(f"[e2e:room]   {line}")
            raise

        # The overlay panel must become visible.
        try:
            expect(info_overlay).to_be_visible(timeout=5_000)
        except Exception:
            print(
                "\n[e2e:room] info overlay not visible - console messages from room page:"
            )
            for line in _room_console:
                print(f"[e2e:room]   {line}")
            raise

        # Geometry check: the overlay must fill the full chat aside, not sit
        # beside the messages as a 288px (w-72) side panel.
        overlay_box = info_overlay.bounding_box()
        assert overlay_box is not None, "info panel overlay bounding box is None"
        tolerance = 20  # pixels - accounts for borders and rounding
        assert abs(overlay_box["width"] - aside_box["width"]) <= tolerance, (
            f"Info panel overlay width {overlay_box['width']:.0f}px should match "
            f"chat aside width {aside_box['width']:.0f}px (overlay mode). "
            f"A ~288px width means the old side-by-side bug is back."
        )
