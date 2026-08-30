"""The sidebar Preferences panel, on every module that has one.

Six sidebars share `ui/partials/preferences_popover.html`, so what is pinned
here is the contract that partial exists for: the trigger toggles, and the
panel is dismissible. Four of those sidebars used to be a daisyUI `dropdown`
instead, which opens on `:focus-within` alone - it swallowed the second click,
ignored Escape, and sprang open on Tab.
"""

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

TRIGGER = "button[aria-label='Preferences'][aria-expanded]"
PANEL = "#preferences-panel"


class PreferencesPopoverTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.login_as(self.user)

    def _paths(self):
        from workspace.projects.services.projects import create_project

        project = create_project(self.user, name="Website")
        return {
            "files": "/files",
            "calendar": "/calendar",
            "notes": "/notes",
            "projects": f"/projects/{project.uuid}",
            "chat": "/chat",
            "mail": "/mail",
        }

    def _open_on(self, path):
        """Load *path*, click the trigger, return (trigger, panel)."""
        self.page.goto(f"{self.live_server_url}{path}")
        trigger = self.page.locator(TRIGGER).first
        panel = self.page.locator(PANEL).first
        expect(trigger).to_be_visible()
        expect(panel).to_be_hidden()
        trigger.click()
        expect(panel).to_be_visible()
        return trigger, panel

    def test_a_second_click_closes_the_panel_on_every_module(self):
        for module, path in self._paths().items():
            with self.subTest(module=module):
                trigger, panel = self._open_on(path)
                expect(trigger).to_have_attribute("aria-expanded", "true")
                trigger.click()
                expect(panel).to_be_hidden()
                expect(trigger).to_have_attribute("aria-expanded", "false")

    def test_escape_closes_the_panel_and_hands_focus_back(self):
        trigger, panel = self._open_on("/files")
        self.page.keyboard.press("Escape")
        expect(panel).to_be_hidden()
        expect(trigger).to_be_focused()

    def test_a_click_outside_closes_the_panel(self):
        _trigger, panel = self._open_on("/files")
        self.page.locator("h1:has-text('My Files')").click()
        expect(panel).to_be_hidden()

    def test_focusing_the_trigger_does_not_open_the_panel(self):
        """The keyboard half of the same bug: `:focus-within` made Tab open a
        panel no key could close."""
        self.page.goto(f"{self.live_server_url}/files")
        trigger = self.page.locator(TRIGGER).first
        expect(trigger).to_be_visible()
        trigger.focus()
        expect(trigger).to_be_focused()
        expect(self.page.locator(PANEL).first).to_be_hidden()
