"""Back and forward inside the vault, with the browser's buttons and the page's.

The keys live in a closure that a page load takes with it, so the only back a
vault can afford is one that never reloads. Each walk marks the window before
it moves and checks the mark is still there after: a reload would clear it,
and lock the vault on the way.
"""

from .test_browser import SIDEBAR, VaultBrowserCase

HEADING = "h1[x-text='heading()']"
BACK = "button[title='Back']"
FORWARD = "button[title='Forward']"


class VaultHistoryTests(VaultBrowserCase):
    def _expect_heading(self, text):
        self.page.wait_for_function(
            "([selector, text]) => document.querySelector(selector)?.textContent.trim() === text",
            arg=[HEADING, text],
            timeout=15000,
        )

    def _create_folder(self, name):
        self.page.get_by_role("button", name="New", exact=True).click()
        self.page.get_by_role("button", name="New folder").first.click()
        self.page.fill(".modal-box input[type=text]", name)
        self.page.click(".modal-box button:has-text('Create')")
        self.page.wait_for_selector(f"tbody tr:has-text('{name}')", timeout=30000)

    def _walk_to_a_folder_then_favorites(self):
        self._open_vault()
        self._create_folder("Banking")
        self.page.click("tbody tr:has-text('Banking')")
        self._expect_heading("Banking")
        self.page.locator(SIDEBAR).get_by_role("button", name="Favorites").click()
        self._expect_heading("Favorites")
        self.page.evaluate("window.__sameDocument = true")

    def _assert_still_open(self):
        self.assertTrue(
            self.page.evaluate("window.__sameDocument === true"),
            "the page reloaded",
        )
        self.assertTrue(self.page.evaluate("window.vaultSession.isUnlocked()"))
        self.assertEqual(
            self.page.locator("input[autocomplete='current-password']").count(), 0
        )
        self.assertTrue(self.page.url.endswith(f"/vault/{self.vault_uuid}"))

    def test_the_browser_back_button_walks_the_vault_without_reloading(self):
        self._walk_to_a_folder_then_favorites()

        self.page.go_back()
        self._expect_heading("Banking")
        self.page.go_back()
        self._expect_heading("All entries")
        self.page.go_forward()
        self._expect_heading("Banking")
        self._assert_still_open()

    def test_the_page_back_and_forward_agree_with_the_browser(self):
        self._walk_to_a_folder_then_favorites()

        self.page.click(BACK)
        self._expect_heading("Banking")
        self.page.click(BACK)
        self._expect_heading("All entries")
        # The onboarding page is still behind in the browser's history, and
        # the vault's own Back must not step out onto it.
        self.assertTrue(self.page.is_disabled(BACK))
        self.page.click(FORWARD)
        self._expect_heading("Banking")

        self.page.go_back()
        self._expect_heading("All entries")
        self.assertTrue(self.page.is_enabled(FORWARD))
        self.page.go_forward()
        self.page.go_forward()
        self._expect_heading("Favorites")
        self.assertTrue(self.page.is_disabled(FORWARD))
        self.assertTrue(self.page.is_enabled(BACK))
        self._assert_still_open()

    def test_back_after_switching_vault_returns_to_the_vault_left(self):
        self._open_vault()
        self.page.evaluate("window.__sameDocument = true")
        self.page.click("[data-testid='vault-switcher']")
        self.page.click("text=New vault")
        self.page.wait_for_selector(".modal-box input[type=text]")
        self.page.fill(".modal-box input[type=text] >> nth=0", "Work")
        self.page.click(".modal-box button:has-text('Create')")
        self._wait_for_switcher_named("Work")
        self.assertFalse(self.page.url.endswith(f"/vault/{self.vault_uuid}"))

        self.page.go_back()
        self._wait_for_switcher_named("Personal")
        self._assert_still_open()
