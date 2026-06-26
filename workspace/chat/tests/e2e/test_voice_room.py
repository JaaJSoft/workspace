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

        # Step 4: Navigate the MAIN tab to /files - a full page reload to another
        # module. Under the old architecture the call lived in the chat page and
        # would have been destroyed when the Alpine app tore down here.
        self.page.goto(f"{self.live_server_url}/files")
        self.page.wait_for_load_state("domcontentloaded")

        # Step 5: The room tab must still be alive and still show the participant
        # tile. This is the core regression assertion: because the call is owned
        # by the room tab (not the main tab), main-tab navigation cannot kill it.
        expect(participants_section.get_by_text("voice-tester")).to_be_visible(
            timeout=5_000
        )
