"""E2E: Enter in an AppDialog answers with the button that has the focus.

Skipped unless E2E=1.
"""

from __future__ import annotations

from workspace.common.tests.e2e.base import PlaywrightTestCase


class AppDialogEnterKeyTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user(username="dialog-keys"))
        self.page.goto(f"{self.live_server_url}/users/settings")

    def _answer_with_enter_on(self, kind, button_id):
        self.page.evaluate(
            f"() => {{ window.__answer = 'pending';"
            f" AppDialog.{kind}({{ title: 'Delete' }}).then((a) => {{ window.__answer = a; }}); }}"
        )
        self.page.locator(f"#{button_id}").focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("window.__answer !== 'pending'")
        return self.page.evaluate("window.__answer")

    def test_enter_on_cancel_cancels_a_confirm(self):
        self.assertIs(self._answer_with_enter_on("confirm", "app-dialog-confirm-cancel"), False)

    def test_enter_on_ok_confirms(self):
        self.assertIs(self._answer_with_enter_on("confirm", "app-dialog-confirm-ok"), True)

    def test_enter_on_cancel_cancels_a_prompt(self):
        self.assertIsNone(self._answer_with_enter_on("prompt", "app-dialog-prompt-cancel"))
