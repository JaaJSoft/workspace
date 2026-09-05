"""The password generator, driven in a real browser.

The unit tests prove the drawing; they cannot prove the wiring, and the wiring
is where this feature can quietly do nothing. Alpine binds an `x-if` panel, an
event crosses two components, and the value has to survive a save, a re-open
and a decryption before it is the entry's password. Only a walk sees that.

The second property is the one the module cares about: a generated password is
a plaintext held outside any entry, so a lock has to take it back the way it
takes a draft back.
"""

from django.core.cache import cache

from workspace.common.tests.e2e.base import PlaywrightTestCase

GOOD_PASSWORD = "correct-horse-battery-staple-42"
CORPUS_ROUTE = "https://api.pwnedpasswords.com/range/*"

PANEL = "aside:has(button[aria-label='Close the panel'])"
GENERATOR = ".modal-box div:has(> .tabs)"
PREVIEW = ".modal-box .font-mono.break-all"


class GeneratorWalkTests(PlaywrightTestCase):
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
        self.page.check("#recovery-key-acknowledged")
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
        self._onboard()
        self._unlock()
        self.page.wait_for_selector("text=All entries", timeout=30000)
        self.page.wait_for_url("**/vault/*", timeout=30000)

    def _open_new_login(self):
        self.page.get_by_role("button", name="New", exact=True).click()
        self.page.get_by_role("button", name="New login").first.click()
        self.page.wait_for_selector(".modal-box input[type=text]")

    def test_a_generated_password_is_the_one_the_entry_stores(self):
        self._open_vault()
        self._open_new_login()
        self.page.fill(".modal-box input[type=text] >> nth=0", "GitHub")

        self.page.click("button[aria-label='Generate a password']")
        self.page.wait_for_selector(PREVIEW, timeout=10000)
        drawn = self.page.inner_text(PREVIEW)
        self.assertTrue(drawn, "the panel opened without drawing anything")

        self.page.click(".modal-box button:has-text('Use')")
        # The panel closes on apply, and the field it wrote into now holds it.
        self.page.wait_for_selector(PREVIEW, state="detached", timeout=10000)
        self.assertEqual(
            self.page.input_value(".modal-box input[type=password]"), drawn
        )

        self.page.click(".modal-box button:has-text('Save')")
        self.page.wait_for_selector("tbody tr:has-text('GitHub')", timeout=30000)

        # Sealed, stored, fetched back and opened: the round trip is what says
        # the generated value is the entry's password and not just a string
        # that once sat in an input.
        self.page.click("tbody tr:has-text('GitHub')")
        self.page.wait_for_selector("button[aria-label='Reveal the password']")
        self.page.click("button[aria-label='Reveal the password']")
        self.page.wait_for_selector("button[aria-label='Hide the password']")
        self.assertIn(drawn, self.page.locator(PANEL).inner_text())

    def test_only_a_field_the_schema_marks_offers_a_generator(self):
        # Three fields are on the form - username, password, website - and the
        # button belongs to the one types.py declares generatable. A template
        # deciding for itself would put one on all three.
        self._open_vault()
        self._open_new_login()
        self.assertEqual(
            self.page.locator(".modal-box button[aria-label^='Generate a']").count(), 1
        )
        self.assertEqual(
            self.page.get_by_role("button", name="Generate a password").count(), 1
        )

    def test_locking_takes_the_generated_password_off_the_screen(self):
        self._open_vault()
        self.page.click("a:has-text('Generator'), li:has-text('Generator') button")
        self.page.wait_for_selector(PREVIEW, timeout=10000)
        drawn = self.page.inner_text(PREVIEW)
        self.assertTrue(drawn)

        # Locked from the session rather than from the sidebar: the dialog's
        # overlay covers the sidebar, and the case that matters is the idle
        # lock firing while the panel is open - which is exactly this call.
        self.page.evaluate("() => window.vaultSession.lock()")
        self.page.wait_for_selector(
            "input[autocomplete='current-password']", timeout=15000
        )
        self.assertNotIn(drawn, self.page.locator("body").inner_text())

    def test_the_standalone_generator_copies_through_the_clearing_clipboard(self):
        # Not navigator.clipboard on its own: the banner is what says the
        # module's clearing timer took the value, as it does for every other
        # secret the vault copies.
        self._open_vault()
        self.page.click("a:has-text('Generator'), li:has-text('Generator') button")
        self.page.wait_for_selector(PREVIEW, timeout=10000)
        drawn = self.page.inner_text(PREVIEW)

        self.page.click(".modal-box button:has-text('Copy')")
        self.page.wait_for_selector("text=Password copied", timeout=10000)
        self.assertEqual(
            self.page.evaluate("() => navigator.clipboard.readText()"), drawn
        )

    def test_the_generator_answers_a_passphrase_when_asked_for_one(self):
        self._open_vault()
        self.page.click("a:has-text('Generator'), li:has-text('Generator') button")
        self.page.wait_for_selector(PREVIEW, timeout=10000)

        self.page.click(".modal-box button:has-text('Passphrase')")
        self.page.wait_for_function(
            "() => document.querySelector('.modal-box .font-mono.break-all')"
            "?.innerText.includes('-')",
            timeout=10000,
        )
        phrase = self.page.inner_text(PREVIEW)
        self.assertGreaterEqual(len(phrase.split("-")), 6)

    def test_opening_the_generator_adds_no_field_that_could_submit_the_form(self):
        # The panel is embedded in the entry form, where a text-like input
        # submits on Enter: choosing a separator would save the record -
        # without the generated password, which has not been applied yet - and
        # tear the panel down with the drawn value still in it.
        #
        # The separator is a <select> for that reason, and neither a select nor
        # a range triggers implicit submission. Counting rather than guarding:
        # a text field coming back here is what has to fail, and a guard on a
        # control that cannot submit would be untestable decoration.
        self._open_vault()
        self._open_new_login()
        texts = ".modal-box input:not([type=range]):not([type=checkbox])"
        before = self.page.locator(texts).count()

        self.page.click("button[aria-label='Generate a password']")
        self.page.click(".modal-box button:has-text('Passphrase')")
        self.page.wait_for_selector(".modal-box select.select-xs")

        self.assertEqual(self.page.locator(texts).count(), before)
