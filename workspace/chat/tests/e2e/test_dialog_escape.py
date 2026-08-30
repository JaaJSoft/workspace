"""E2E test: a representative ``<dialog>`` modal closes when the user
presses Escape.

We pick the chat "New conversation" dialog as a representative target:
it is reachable in a single click from ``/chat``, requires no fixture
data, and combines the two close mechanisms a typical modal in this
codebase relies on — the browser's native ``<dialog>`` ESC handling
*and* an explicit Alpine ``@keydown.escape="$refs.newConvDialog.close()"``
handler on the element.

This test isn't there to validate any particular implementation; it
locks down the user-facing UX: pressing Escape on an open modal closes
it. It catches the regression introduced when a ``<dialog>`` is swapped
for a custom ``<div>`` overlay (losing the native handler) and the dev
forgets to wire an explicit ESC binding.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase


class DialogEscapeTests(PlaywrightTestCase):
    """Pressing Escape on the chat "New conversation" modal closes it."""

    def test_new_conversation_dialog_closes_on_escape(self):
        user = self.create_user(username="alice")
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/chat")

        # The "New conversation" trigger is a square icon button in the
        # sidebar header — no visible label, only a ``title`` attribute.
        # ``get_by_title`` matches the full string including the shortcut
        # hint, which is the most stable selector (the lucide icon name
        # could be swapped without changing UX).
        self.page.get_by_title("New conversation (Alt+N)").click()

        # The dialog is opened via ``showModal()``, which sets the
        # native ``[open]`` attribute. We target it by its unique
        # ``<h3>`` heading: matching by ``has_text`` would also catch
        # the chat help dialog (which lists "New conversation" in a
        # keyboard-shortcut table row).
        dialog = self.page.locator("dialog.modal").filter(
            has=self.page.get_by_role("heading", name="New conversation", exact=True),
        )
        expect(dialog).to_have_attribute("open", "")

        # Click inside the dialog to put keyboard focus on the search
        # input. The trigger click that opened the dialog left focus on
        # the trigger button (outside the dialog), and a global
        # ``page.keyboard.press("Escape")`` from there never bubbles to
        # the dialog's ``@keydown.escape`` handler. Clicking the input
        # is the same focus transfer a user would naturally do before
        # pressing Escape.
        dialog.locator("input").first.click()
        self.page.keyboard.press("Escape")

        # Assert the close via the native ``[open]`` attribute, not CSS
        # visibility. daisyUI styles ``.modal`` with ``display: grid``
        # and a fade-out opacity transition, so a *just*-closed dialog
        # stays in the layout for ~150 ms while opacity animates to 0
        # and Playwright's ``to_be_visible`` flags it as still visible.
        # The ``[open]`` attribute is the source of truth and flips
        # synchronously when ``dialog.close()`` is called.
        expect(dialog).not_to_have_attribute("open", "")
