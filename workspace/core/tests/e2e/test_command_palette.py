"""The command palette's actions-only mode.

Ctrl+Shift+K (or a leading ``>``) narrows the palette to the workspace
commands - the apps and the actions they register - and leaves files, notes
and mails out. The list is filtered from the commands the page already
embeds, so the unified search endpoint is never asked.
"""

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

PALETTE = ".navbar [x-data*='commandPaletteDropdown']"
INPUT = f"{PALETTE} input"
JOURNAL = f"{PALETTE} a[href='/notes?view=journal']"
NOTES_APP = f"{PALETTE} a[href='/notes']"


class CommandPaletteActionsModeTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user(username="alice"))
        self.search_requests = []
        self.page.on(
            "request",
            lambda request: (
                self.search_requests.append(request.url)
                if "/api/v1/search" in request.url
                else None
            ),
        )
        self.page.goto(f"{self.live_server_url}/users/settings")
        self.page.wait_for_selector(INPUT)

    def test_ctrl_shift_k_lists_every_action_without_searching(self):
        self.page.keyboard.press("Control+Shift+K")
        self.page.wait_for_selector(JOURNAL, state="visible")

        self.assertEqual(self.page.input_value(INPUT), ">")
        self.assertTrue(self.page.is_visible(NOTES_APP))
        # Retried rather than read once: enterCommandMode opens the dropdown
        # and focuses the input in a $nextTick, so the wait above can return
        # in between the two. Naming the palette's own input also says more
        # than asking whether some input somewhere holds the focus - and
        # .first because the navbar carries the palette twice, desktop and
        # mobile, which the other assertions here already resolve that way.
        expect(self.page.locator(INPUT).first).to_be_focused()
        self.assertEqual(self.search_requests, [])

    def test_typing_after_the_prefix_narrows_the_actions(self):
        self.page.click(INPUT)
        self.page.keyboard.type(">diary")
        self.page.wait_for_selector(JOURNAL, state="visible")

        self.assertFalse(self.page.is_visible(NOTES_APP))
        self.assertEqual(self.search_requests, [])

    def test_plain_text_still_searches_everything(self):
        self.page.click(INPUT)
        with self.page.expect_request(
            lambda request: "/api/v1/search" in request.url
        ) as searched:
            self.page.keyboard.type("diary")

        self.assertIn("q=diary", searched.value.url)
