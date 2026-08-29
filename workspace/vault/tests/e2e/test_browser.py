"""The browser screen, driven the way a person drives it.

Everything else in this module tests a layer: the crypto against vectors, the
API against a client, the reader against stubs. This walks the page - create,
browse, open, copy, lock - because the properties worth the most here are the
ones no layer can see on its own:

  - a listing decrypts a name and a login and no secret, which is a claim
    about what the page holds while it sits open;
  - a copy puts a real plaintext on a real clipboard and takes it back;
  - a row whose signature was forged behind the client disappears, and its
    name never renders.

The forged row is written straight into the database on purpose. The API
verifies every signature it is handed, so there is no way to produce one
through it - which is exactly what makes the database the right place to
imitate a hostile server.
"""

from django.core.cache import cache

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.vault.models import VaultEntry

GOOD_PASSWORD = "correct-horse-battery-staple-42"
CORPUS_ROUTE = "https://api.pwnedpasswords.com/range/*"
# The properties panel, named by its own close button: the page holds two
# other <aside> elements, and "the one with a Username label" would match the
# sidebar the moment a tag is called that.
PANEL = "aside:has(button[aria-label='Close the panel'])"


class VaultBrowserTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="owner", email="owner@example.com")
        self.login_as(self.user)
        self.page.route(
            CORPUS_ROUTE,
            lambda route: route.fulfill(
                status=200, body="0000000000000000000000000000000000000:1\n"
            ),
        )
        self.context.grant_permissions(["clipboard-read", "clipboard-write"])

    def tearDown(self):
        cache.clear()
        super().tearDown()

    # ---- getting to an open vault ----------------------------------------

    def _onboard(self):
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.wait_for_url("**/vault/onboarding")
        self.page.click("button:has-text('I understand')")
        self.page.fill("input[autocomplete='new-password'] >> nth=0", GOOD_PASSWORD)
        self.page.fill("input[autocomplete='new-password'] >> nth=1", GOOD_PASSWORD)
        self.page.wait_for_selector(
            "button:has-text('Set my master password'):not([disabled])", timeout=15000
        )
        self.page.click("button:has-text('Set my master password')")
        self.page.wait_for_selector("#recovery-key-acknowledged", timeout=60000)
        self.secret = self.page.inner_text("[data-recovery-key]")
        self.page.check("#recovery-key-acknowledged")
        # Remembered on this device, so every later unlock in this walk needs
        # the master password alone - as it does for a real user who ticked it.
        self.page.check("input.checkbox.checkbox-sm")
        self.page.click("button:has-text('Create my first vault')")
        self.page.wait_for_url("**/vault")

    def _unlock(self):
        self.page.wait_for_selector("input[autocomplete='current-password']")
        self.page.fill("input[autocomplete='current-password']", GOOD_PASSWORD)
        self.page.click("button:has-text('Unlock')")
        self.page.wait_for_function(
            "() => window.vaultSession && window.vaultSession.isUnlocked()",
            timeout=60000,
        )

    def _open_vault(self):
        """Onboard, open the one vault, and land on the browser screen."""
        self._onboard()
        self._unlock()
        self.page.wait_for_selector('a[href^="/vault/"]', timeout=30000)
        self.vault_uuid = self.page.get_attribute('a[href^="/vault/"]', "href").rsplit(
            "/", 1
        )[1]
        self.page.goto(f"{self.live_server_url}/vault/{self.vault_uuid}")
        self._unlock()
        self.page.wait_for_selector("text=All entries", timeout=30000)

    def _create_entry(self, name, username, password):
        # get_by_role with exact=True: the navbar carries a "What's new"
        # control, and a substring match reaches it first.
        self.page.get_by_role("button", name="New", exact=True).click()
        self.page.get_by_text("New login").click()
        self.page.wait_for_selector(".modal-box input[type=text]")
        self.page.fill(".modal-box input[type=text] >> nth=0", name)
        self.page.fill(".modal-box input[type=text] >> nth=1", username)
        self.page.fill(".modal-box input[type=password]", password)
        self.page.click(".modal-box button:has-text('Save')")
        self.page.wait_for_selector(f"tbody tr:has-text('{name}')", timeout=30000)

    # ---- the walk ---------------------------------------------------------

    def test_an_entry_is_created_browsed_and_read_back(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")

        # The row shows what the listing is allowed to open, and no more.
        row = self.page.locator("tbody tr:has-text('GitHub')")
        self.assertIn("octocat", row.inner_text())
        self.assertNotIn("hunter2", self.page.locator("body").inner_text())

        self.page.click("tbody tr:has-text('GitHub')")
        panel = self.page.locator(PANEL)
        panel.wait_for(timeout=10000)
        self.assertIn("octocat", panel.inner_text())
        self.assertNotIn("hunter2", panel.inner_text())

    def test_a_password_is_opened_only_when_it_is_copied(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")
        self.page.click("tbody tr:has-text('GitHub')")
        self.page.wait_for_selector("button[aria-label='Copy the password']")
        self.page.click("button[aria-label='Copy the password']")
        self.page.wait_for_selector("text=Password copied", timeout=10000)
        self.assertEqual(
            self.page.evaluate("() => navigator.clipboard.readText()"), "hunter2"
        )

        self.page.click("button:has-text('Clear now')")
        self.page.wait_for_selector("text=Password copied", state="hidden")
        self.assertEqual(self.page.evaluate("() => navigator.clipboard.readText()"), "")

    def test_locking_empties_the_screen_and_the_clipboard(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")
        self.page.click("tbody tr:has-text('GitHub')")
        self.page.click("button[aria-label='Copy the password']")
        self.page.wait_for_selector("text=Password copied", timeout=10000)

        self.page.get_by_role("button", name="Lock", exact=True).click()
        self.page.wait_for_selector("input[autocomplete='current-password']")
        # A secret on the clipboard outlives the keys that opened it, so the
        # lock takes it back rather than leaving it for the next person here.
        self.assertEqual(self.page.evaluate("() => navigator.clipboard.readText()"), "")
        self.assertNotIn("octocat", self.page.locator("body").inner_text())

    def test_a_forged_signature_removes_the_row_and_raises_the_banner(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")

        entry = VaultEntry.objects.get(vault__uuid=self.vault_uuid)
        VaultEntry.objects.filter(uuid=entry.uuid).update(
            metadata_sig="A" * len(entry.metadata_sig)
        )

        self.page.reload()
        self._unlock()
        self.page.wait_for_selector("text=removed from the list", timeout=30000)
        body = self.page.locator("body").inner_text()
        # The count is all that survives: the name is not shown "just to help
        # identify it", and neither is the login the listing would have opened.
        self.assertNotIn("GitHub", body)
        self.assertNotIn("octocat", body)

    def test_the_trash_takes_a_row_and_gives_it_back(self):
        self._open_vault()
        self._create_entry("GitHub", "octocat", "hunter2")

        self.page.click("tbody tr:has-text('GitHub')")
        self.page.click(f"{PANEL} button[aria-label='Move to trash']")
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", state="detached")

        # Not exact: the sidebar entry carries a count badge, so its
        # accessible name grows a number the moment the trash is not empty.
        self.page.get_by_role("button", name="Trash").first.click()
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", timeout=10000)
        self.page.click("tbody tr:has-text('GitHub')")
        self.page.click(f"{PANEL} button:has-text('Restore')")
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", state="detached")

        self.page.get_by_role("button", name="All entries").first.click()
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", timeout=10000)
