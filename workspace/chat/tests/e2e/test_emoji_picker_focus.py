"""E2E test: opening the chat emoji picker puts the caret in its search
field, and closing it hands focus back to the message textarea.

The behaviour is pure focus management across a shadow-DOM boundary, so a
real browser is the only place it can be observed: the search field belongs
to the ``emoji-picker-element`` web component's shadow root, and a focus
inside a shadow root surfaces on ``document.activeElement`` as the *host*
element, not the field itself.

Running against the real component (rather than a stub) is deliberate: the
fragile half of the fix is the ``input.search`` selector, which only this
test pins down. A component upgrade renaming that class would otherwise
silently stop focusing anything, with no failure anywhere.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember
from workspace.common.tests.e2e.base import PlaywrightTestCase


class EmojiPickerFocusTests(PlaywrightTestCase):
    """The composer stays typeable while the emoji picker is open."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="emoji-tester")
        peer = self.create_user(username="emoji-peer")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=peer)
        self.login_as(self.user)

    def _open_conversation(self):
        self.page.goto(f"{self.live_server_url}/chat/{self.conv.uuid}")
        expect(
            self.page.locator('textarea[placeholder="Type a message..."]')
        ).to_be_visible()
        # The debug toolbar overlays the page when DEBUG is on and swallows
        # clicks aimed at the composer.
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        # The picker's module builds the shadow DOM on upgrade, so wait for
        # the search field to exist before driving it.
        self.page.wait_for_function(
            "() => document.querySelector('emoji-picker')"
            "?.shadowRoot?.querySelector('input.search')",
        )
        # Selecting a conversation ends, after the messages, read receipts and
        # pins have loaded, by focusing the composer. A picker opened before
        # that last step has its search field's focus stolen by it, so wait
        # for the load to be over before driving the picker.
        self.page.wait_for_function(
            "() => document.activeElement?.matches("
            "'textarea[placeholder=\"Type a message...\"]')",
        )

    def _wait_for_search_field_focus(self):
        # The focus lands after the click has returned: Alpine reveals the
        # x-show wrapper on the next animation frame (so the opening click
        # does not trip @click.outside) and only then does the deferred
        # focus() find a rendered field. Reading activeElement right after
        # click() races that, so poll for it instead.
        self.page.wait_for_function(
            """() => {
                const picker = document.querySelector('emoji-picker');
                return document.activeElement === picker
                    && picker.shadowRoot.activeElement
                    === picker.shadowRoot.querySelector('input.search');
            }""",
        )

    def _textarea_has_focus(self):
        return self.page.evaluate(
            "() => document.activeElement?.matches('textarea[placeholder=\"Type a message...\"]')",
        )

    def test_opening_the_picker_focuses_the_search_field(self):
        self._open_conversation()
        self.page.get_by_title("Emoji", exact=True).click()

        self._wait_for_search_field_focus()

        # The whole point of the focus: the next keystrokes filter the grid
        # instead of going nowhere.
        self.page.keyboard.type("cat")
        self.assertEqual(
            self.page.evaluate(
                "() => document.querySelector('emoji-picker')"
                ".shadowRoot.querySelector('input.search').value",
            ),
            "cat",
        )

    def test_escape_closes_the_picker_and_refocuses_the_composer(self):
        self._open_conversation()
        picker = self.page.locator("div:has(> emoji-picker)")
        self.page.get_by_title("Emoji", exact=True).click()
        self._wait_for_search_field_focus()

        # Escape is pressed with the caret in the picker, so the composer's
        # own keydown handler never sees it - the picker wrapper must.
        self.page.keyboard.press("Escape")

        expect(picker).to_be_hidden()
        self.assertTrue(self._textarea_has_focus())

    def test_clicking_outside_closes_the_picker_and_refocuses_the_composer(self):
        self._open_conversation()
        picker = self.page.locator("div:has(> emoji-picker)")
        self.page.get_by_title("Emoji", exact=True).click()
        self._wait_for_search_field_focus()

        # Empty space in the middle of the message area: no focusable element
        # takes the focus, so the browser drops it on <body> — the case that
        # would leave the composer dead if closing did not hand it back.
        self.page.mouse.click(900, 250)

        expect(picker).to_be_hidden()
        self.assertTrue(self._textarea_has_focus())

    def test_closing_a_reaction_picker_leaves_the_composer_alone(self):
        self._open_conversation()
        composer = self.page.locator('textarea[placeholder="Type a message..."]')
        composer.fill("hi")
        composer.press("Enter")
        # The optimistic bubble is replaced by the server-rendered one, which
        # is the copy that carries the hover toolbar.
        message = self.page.locator("#messages-container .group\\/msg").last
        expect(message.get_by_title("More reactions")).to_be_attached()
        picker = self.page.locator("div:has(> emoji-picker)")

        # The reaction toolbar only materialises on hover - which a tap also
        # latches on a touch device, so this path is reachable on a phone.
        message.hover()
        message.get_by_title("More reactions").click()
        self._wait_for_search_field_focus()

        self.page.keyboard.press("Escape")

        expect(picker).to_be_hidden()
        # Reacting never involved the composer, and landing the caret there
        # would raise the virtual keyboard on a phone for nothing.
        self.assertFalse(self._textarea_has_focus())

    def test_clicking_another_control_does_not_steal_its_focus(self):
        self._open_conversation()
        picker = self.page.locator("div:has(> emoji-picker)")
        self.page.get_by_title("Emoji", exact=True).click()
        self._wait_for_search_field_focus()

        search_box = self.page.locator('input[placeholder="Search conversations"]')
        search_box.click()

        expect(picker).to_be_hidden()
        # Handing focus to the composer here would swallow the words the user
        # is about to type into the sidebar search.
        expect(search_box).to_be_focused()
