"""E2E: a progress row ends when its own call ends, not when the next starts.

A round dispatches independent tool calls together, so their rows arrive
together and finish in any order. Which one still reads as running cannot be
deduced from their order any more - the browser is told per call.

Drives the payloads the step mailbox really produces through the same window
event the SSE client dispatches, so everything from the Alpine binding down
is the production path.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import os

from playwright.sync_api import expect

from workspace.ai.harness.model import ToolCall
from workspace.ai.services.stream_steps import (
    notify_tool_step,
    notify_tool_step_done,
    read_steps,
)
from workspace.chat.models import Conversation, ConversationMember
from workspace.common.tests.e2e.base import PlaywrightTestCase

SHOTS = os.environ.get("SHOTS")


def _tool_call(call_id, name, arguments="{}"):
    return ToolCall(id=call_id, name=name, arguments=arguments)


class BotStepRowTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="step-watcher", password="pass12345")
        self.peer = self.create_user(username="step-peer")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        for member in (self.user, self.peer):
            ConversationMember.objects.create(
                conversation=self.conversation, user=member
            )
        self._cursor = None

        self.profile_call = _tool_call("call_profile", "get_current_user_info")
        self.weather_call = _tool_call(
            "call_weather", "get_weather", '{"location": "Lyon"}'
        )

    def _deliver(self):
        """Hand the browser what the mailbox holds, as the SSE client does."""
        envelopes, self._cursor = read_steps(self.user.id, self._cursor)
        for envelope in envelopes:
            self.page.evaluate(
                "data => window.dispatchEvent("
                "new CustomEvent('ai-stream-bot_step', { detail: data }))",
                envelope["data"],
            )

    def _open_conversation(self):
        """Land straight in the conversation - the sidebar it would be
        picked from is collapsed at a mobile viewport."""
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat/{self.conversation.pk}")
        expect(self.page.locator("#messages-container")).to_be_attached()

    def _shoot(self, name):
        """Capture the pane when SHOTS names a directory (PR screenshots)."""
        if SHOTS:
            self.page.screenshot(path=f"{SHOTS}/{name}.png", full_page=False)

    def test_a_row_ends_when_its_own_call_ends(self):
        self._open_conversation()

        notify_tool_step([self.user.id], self.conversation.pk, self.profile_call)
        notify_tool_step([self.user.id], self.conversation.pk, self.weather_call)
        self._deliver()

        # Both calls were dispatched together, so both rows read as running.
        expect(self.page.get_by_text("Looking up profile")).to_be_visible()
        expect(self.page.get_by_text("Checking the weather")).to_be_visible()
        expect(self.page.get_by_text("Looked up profile")).to_be_hidden()
        self._shoot("steps-both-running")

        notify_tool_step_done([self.user.id], self.conversation.pk, self.profile_call)
        self._deliver()

        # The first call ended; the second, still in flight, is the one shown
        # last and would have been the only running row under the old rule.
        expect(self.page.get_by_text("Looked up profile")).to_be_visible()
        expect(self.page.get_by_text("Looking up profile")).to_be_hidden()
        expect(self.page.get_by_text("Checking the weather")).to_be_visible()
        self._shoot("steps-one-done")

        notify_tool_step_done([self.user.id], self.conversation.pk, self.weather_call)
        self._deliver()

        expect(self.page.get_by_text("Checked the weather")).to_be_visible()
        expect(self.page.get_by_text("Checking the weather")).to_be_hidden()
        # Every tool has reported back and the reply is still being written:
        # the dots are what says the bot has not gone quiet.
        expect(self.page.locator(".ai-thinking-dots")).to_be_visible()
        self._shoot("steps-all-done")

    def test_rows_survive_a_narrow_pane(self):
        # The detail truncates at this width; the label and its marker are
        # what the row is for and must stay readable.
        self.page.set_viewport_size({"width": 390, "height": 844})
        self._open_conversation()

        notify_tool_step([self.user.id], self.conversation.pk, self.profile_call)
        notify_tool_step([self.user.id], self.conversation.pk, self.weather_call)
        notify_tool_step_done([self.user.id], self.conversation.pk, self.profile_call)
        self._deliver()

        expect(self.page.get_by_text("Looked up profile")).to_be_visible()
        expect(self.page.get_by_text("Checking the weather")).to_be_visible()
        self._shoot("steps-mobile")

    def test_rows_keep_their_call_order_when_the_second_ends_first(self):
        self._open_conversation()

        notify_tool_step([self.user.id], self.conversation.pk, self.profile_call)
        notify_tool_step([self.user.id], self.conversation.pk, self.weather_call)
        notify_tool_step_done([self.user.id], self.conversation.pk, self.weather_call)
        self._deliver()

        # Exactly one row reads as finished: the second, whose call ended.
        expect(self.page.locator(".ai-step-label-done:visible")).to_have_count(1)
        expect(self.page.get_by_text("Checked the weather")).to_be_visible()
        expect(self.page.get_by_text("Looking up profile")).to_be_visible()
