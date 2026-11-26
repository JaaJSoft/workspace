"""E2E: Enter in an AppDialog answers with the button that has the focus.

Skipped unless E2E=1.
"""

from __future__ import annotations

from playwright.sync_api import expect

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
        self.assertIs(
            self._answer_with_enter_on("confirm", "app-dialog-confirm-cancel"), False
        )

    def test_enter_on_ok_confirms(self):
        self.assertIs(
            self._answer_with_enter_on("confirm", "app-dialog-confirm-ok"), True
        )

    def test_enter_on_cancel_cancels_a_prompt(self):
        self.assertIsNone(
            self._answer_with_enter_on("prompt", "app-dialog-prompt-cancel")
        )


class AppDialogHeaderTests(PlaywrightTestCase):
    """Each AppDialog shows the title it is given, and its icon square only
    when it is given an icon."""

    KINDS = ("confirm", "prompt", "select", "message")

    def setUp(self):
        super().setUp()
        self.login_as(self.create_user(username="dialog-header"))
        self.page.goto(f"{self.live_server_url}/users/settings")

    def _open(self, kind, options):
        self.page.evaluate(
            "([kind, options]) => { AppDialog[kind](options); }", [kind, options]
        )
        dialog = self.page.locator(f"#app-dialog-{kind}")
        expect(dialog).to_be_visible()
        return dialog

    def _close(self, kind):
        self.page.evaluate(f"document.getElementById('app-dialog-{kind}').close()")

    def test_title_and_icon(self):
        for kind in self.KINDS:
            with self.subTest(kind=kind):
                self._open(kind, {"title": "Delete it", "icon": "trash-2"})
                expect(self.page.locator(f"#app-dialog-{kind}-title")).to_have_text(
                    "Delete it"
                )
                icon = self.page.locator(f"#app-dialog-{kind}-icon")
                expect(icon).to_be_visible()
                expect(icon.locator("svg")).to_be_visible()
                self._close(kind)

    def test_no_icon_hides_the_square(self):
        for kind in self.KINDS:
            with self.subTest(kind=kind):
                self._open(kind, {"title": "Plain"})
                expect(self.page.locator(f"#app-dialog-{kind}-title")).to_have_text(
                    "Plain"
                )
                expect(self.page.locator(f"#app-dialog-{kind}-icon")).to_be_hidden()
                self._close(kind)
