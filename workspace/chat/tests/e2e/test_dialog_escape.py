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

        # The dialog is opened via ``showModal()`` — when open, the
        # native ``<dialog>`` element receives the ``open`` attribute
        # and daisyui's ``.modal`` styles make it visible. We assert
        # both: visibility is what the user perceives, ``[open]`` is
        # the precise state of the native element.
        #
        # We target the dialog by its unique ``<h3>`` heading rather than
        # by ``has_text``, because the chat help dialog also contains the
        # text "New conversation" (in a keyboard-shortcut table row).
        dialog = self.page.locator("dialog.modal").filter(
            has=self.page.get_by_role("heading", name="New conversation", exact=True),
        )
        expect(dialog).to_be_visible()
        expect(dialog).to_have_attribute("open", "")

        # Press Escape. ``<dialog>`` element fires ``cancel`` natively
        # AND the Alpine ``@keydown.escape`` handler calls ``close()``
        # — either path must close the dialog.
        self.page.keyboard.press("Escape")

        # daisyui hides ``dialog:not([open])`` via CSS, so visibility
        # tracks the ``open`` attribute. Assert the user-visible outcome.
        expect(dialog).not_to_be_visible()
        expect(dialog).not_to_have_attribute("open", "")
