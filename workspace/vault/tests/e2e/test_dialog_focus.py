"""The two dialogs that hold a secret, driven from the keyboard.

Both are mounted under x-if and drawn as a daisyUI modal rather than a native
<dialog>, so nothing the browser does for a modal happens on its own: the
dialog has to announce itself, take focus when it opens, keep Tab inside, close
on Escape and give focus back to the row that opened it. All of it is a
property of a rendered page.
"""

import re

from .test_browser import SIDEBAR, VaultBrowserCase

# Enough presses to go round either dialog more than once.
TAB_PRESSES = 30

FOCUS_IS_INSIDE = """(selector) => {
    const box = document.querySelector(selector);
    return !!box && box.contains(document.activeElement);
}"""


class DialogFocusTests(VaultBrowserCase):
    def setUp(self):
        super().setUp()
        self._open_vault()

    def _sidebar_row(self, name):
        return self.page.locator(SIDEBAR).get_by_role("button", name=name)

    def _assert_behaves_as_a_modal(self, opener, box_selector, title):
        opener.click()
        self.page.wait_for_selector(box_selector, timeout=15000)
        box = self.page.locator(box_selector)

        self.assertEqual(box.get_attribute("aria-modal"), "true")
        title_id = box.get_attribute("aria-labelledby")
        self.assertEqual(self.page.locator(f"#{title_id}").inner_text().strip(), title)

        self.page.wait_for_function(FOCUS_IS_INSIDE, arg=box_selector, timeout=10000)
        for press in range(TAB_PRESSES):
            self.page.keyboard.press("Tab")
            self.assertTrue(
                self.page.evaluate(FOCUS_IS_INSIDE, box_selector),
                f"Tab press {press + 1} left the dialog",
            )

        self.page.keyboard.press("Escape")
        self.page.wait_for_selector(box_selector, state="detached", timeout=10000)
        self.page.wait_for_function(
            "(el) => document.activeElement === el",
            arg=opener.element_handle(),
            timeout=10000,
        )

    def test_the_password_dialog_is_a_modal_from_the_keyboard(self):
        self._assert_behaves_as_a_modal(
            self._sidebar_row(re.compile(r"^Change (?:your master )?password$")),
            "[role=dialog][aria-labelledby=vault-password-dialog-title]",
            "Change master password",
        )

    def test_the_export_dialog_is_a_modal_from_the_keyboard(self):
        self._assert_behaves_as_a_modal(
            self._sidebar_row(re.compile(r"^Export(?: this account)?$")),
            "[role=dialog][aria-labelledby=vault-export-dialog-title]",
            "Export this account",
        )

    def test_escape_still_closes_the_export_dialog_once_its_step_has_changed(self):
        # The warning step replaces the button that was focused, and focus
        # then falls back to the body - outside the box.
        box_selector = "[role=dialog][aria-labelledby=vault-export-dialog-title]"
        self._sidebar_row(re.compile(r"^Export(?: this account)?$")).click()
        self.page.wait_for_selector(box_selector, timeout=15000)
        self.page.check("input[value='interchange']")
        self.page.locator("[data-testid=export-run]").focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_selector("#vault-export-warning-title", timeout=10000)

        self.page.keyboard.press("Escape")
        self.page.wait_for_selector("#vault-export-warning-title", state="detached", timeout=10000)

    def test_escape_still_closes_the_password_dialog_once_focus_has_left_it(self):
        box_selector = "[role=dialog][aria-labelledby=vault-password-dialog-title]"
        self._sidebar_row(re.compile(r"^Change (?:your master )?password$")).click()
        self.page.wait_for_selector(box_selector, timeout=15000)
        # A control disabled under the cursor drops focus the same way.
        self.page.evaluate("document.activeElement.blur()")

        self.page.keyboard.press("Escape")
        self.page.wait_for_selector(box_selector, state="detached", timeout=10000)
