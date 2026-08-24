"""The navbar buttons that do nothing but broadcast an event.

They carry ``data-dispatch``; ``ui/js/dispatch_action.js`` turns the click into
a window event and the dialog listens for it. Three parts, each covered on its
own - the attribute is markup, the listener has unit tests, the dialog has its
own component - and nothing proving they meet. An inline handler at least
failed loudly in the console.
"""

from workspace.common.tests.e2e.base import PlaywrightTestCase

USER_MENU = ".dropdown.dropdown-end > label[tabindex='0']"


class NavbarDispatchButtonTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user(username="alice"))
        self.page.goto(f"{self.live_server_url}/")
        self.page.wait_for_selector(".navbar")

    def _open_from_user_menu(self, label):
        self.page.click(USER_MENU)
        self.page.click(f'button:has-text("{label}")')

    def _is_open(self, dialog_id):
        self.page.wait_for_function(
            f"document.getElementById('{dialog_id}')?.open === true"
        )
        return self.page.evaluate(f"document.getElementById('{dialog_id}').open")

    def test_whats_new_opens_the_changelog(self):
        self._open_from_user_menu("What's new")
        self.assertTrue(self._is_open("changelog-dialog"))

    def test_welcome_tour_opens_the_onboarding_dialog(self):
        self._open_from_user_menu("Welcome tour")
        self.assertTrue(self._is_open("onboarding-dialog"))

    def test_a_click_outside_a_trigger_broadcasts_nothing(self):
        """The listener is delegated on the document, so every click on the
        page runs it; only the ones inside a trigger may dispatch."""
        self.page.click("body")
        self.page.wait_for_timeout(300)
        for dialog in ("changelog-dialog", "onboarding-dialog"):
            self.assertFalse(
                self.page.evaluate(f"document.getElementById('{dialog}').open"),
                dialog,
            )
