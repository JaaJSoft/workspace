"""E2E: the call banner, the thread panel and the voice room catch up after a stream reconnection.

A call or message event pushed while the stream was down expires from the
server's queue, so the page has to ask again. The stream is stubbed (no event
ever arrives) and ``sse:reconnect`` is dispatched by hand, which is what a page
sees after a gap longer than the queue keeps events.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.cache import cache
from django.test import override_settings
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services.calls import leave_call, start_or_join_call
from workspace.chat.services.posting import post_message
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import PlaywrightTestCase

RECONNECT = "window.dispatchEvent(new CustomEvent('sse:reconnect'))"


# The peer of a call started by the test never sends a heartbeat: keep it fresh
# for the whole test, or the call state endpoint ends the call as stale.
@override_settings(CHAT_CALL_PRESENCE_TTL=600)
class ReconnectCatchUpTestCase(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="reconnect-tester")
        self.peer = self.create_user(username="reconnect-peer")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)
        self.login_as(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open_conversation(self):
        self.page.goto(f"{self.live_server_url}/chat")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.page.locator(f"#conv-item-{self.conv.uuid} > button").click()
        expect(self.page.locator(".chat-rich-input")).to_be_visible()


class CallBannerReconnectTests(ReconnectCatchUpTestCase):
    def _banner(self):
        return self.page.get_by_test_id("call-banner")

    def test_a_call_started_during_the_gap_is_announced(self):
        self._open_conversation()
        expect(self._banner()).to_be_hidden()

        start_or_join_call(self.peer, self.conv.uuid)
        self.page.evaluate(RECONNECT)

        expect(self._banner()).to_be_visible()
        expect(self._banner().get_by_text("Join call")).to_be_visible()

    def test_a_call_ended_during_the_gap_loses_its_banner(self):
        start_or_join_call(self.peer, self.conv.uuid)
        self._open_conversation()
        expect(self._banner()).to_be_visible()

        leave_call(self.peer, self.conv.uuid)
        self.page.evaluate(RECONNECT)

        expect(self._banner()).to_be_hidden()


class ThreadPanelReconnectTests(ReconnectCatchUpTestCase):
    def _post(self, body, **fields):
        return post_message(
            self.conv, self.peer, body, body_html=render_message_body(body), **fields
        )

    def test_a_reply_sent_during_the_gap_shows_in_the_open_panel(self):
        root = self._post("the root of the discussion")
        self._post("the first reply", reply_to=root, thread_root=root)
        self._open_conversation()
        footer = self.page.locator("#messages-container").get_by_test_id(
            "thread-footer"
        )
        expect(footer).to_contain_text("1 reply")
        footer.click()
        panel = self.page.locator("#thread-messages-container")
        expect(panel.get_by_text("the first reply")).to_be_visible()
        self.page.wait_for_load_state("networkidle")

        body = "a reply written while the stream was away"
        self._post(body, reply_to=root, thread_root=root)
        self.page.evaluate(RECONNECT)

        expect(footer).to_contain_text("2 replies")
        expect(panel.get_by_text(body)).to_be_visible()


class VoiceRoomReconnectTests(ReconnectCatchUpTestCase):
    @classmethod
    def setUpClass(cls):
        # The room joins the call on load, which needs a microphone: relaunch
        # Chromium with a fake one.
        super().setUpClass()
        cls.browser.close()
        cls.browser = getattr(cls._playwright, cls.BROWSER_NAME).launch(
            headless=cls.HEADLESS,
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
            ],
        )

    def setUp(self):
        super().setUp()
        self.context.grant_permissions(["microphone"])

    def test_a_message_sent_during_the_gap_shows_in_the_room(self):
        self.page.goto(f"{self.live_server_url}/chat/room/{self.conv.uuid}")
        expect(
            self.page.get_by_test_id("participants-grid").get_by_text(
                "reconnect-tester"
            )
        ).to_be_visible(timeout=15_000)
        # The join reloads the list once, to show its own system message: let
        # it land first, or it brings the message in instead of the reconnect.
        messages = self.page.locator("#messages-container")
        expect(messages.get_by_text("Call started")).to_be_visible()
        self.page.wait_for_load_state("networkidle")

        body = "written while the room was away"
        post_message(self.conv, self.peer, body, body_html=render_message_body(body))
        self.page.evaluate(RECONNECT)

        expect(messages.get_by_text(body)).to_be_visible()
