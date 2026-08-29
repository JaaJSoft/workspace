"""Unlocking, in a real browser, against the real bundle.

The walk starts from onboarding because that is the only way to get an account
whose sealed keys correspond to a password a test knows. What it then proves is
the part no unit test reaches: a wrong password fails on an AEAD tag with no
request leaving the page, the right one opens the vault created at the end of
onboarding, and locking empties the screen.
"""

from django.core.cache import cache

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.vault.models import Vault, VaultKeyWrap

GOOD_PASSWORD = "correct-horse-battery-staple-42"
CORPUS_ROUTE = "https://api.pwnedpasswords.com/range/*"

# By the label it sits in, not by type: the shared layout's drawer toggle and
# the navbar's theme switch are checkboxes too, and both come earlier in the
# document than this one.
REMEMBER_CHECKBOX = (
    "label:has-text('Remember my recovery key on this device') input[type='checkbox']"
)


class UnlockWalkTests(PlaywrightTestCase):
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

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _onboard(self):
        """Walk onboarding and return the recovery key it displayed."""
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
        # Left unticked on purpose: the reload tests below have to prove the
        # recovery key is asked for when it was never stored.
        self.page.click("button:has-text('Create my first vault')")
        self.page.wait_for_url("**/vault")
        return self.secret

    def _unlock(self, password=GOOD_PASSWORD, secret=None):
        """Fill and submit the unlock form, recovery key included.

        The recovery key was never remembered by ``_onboard``, so
        ``secretMissing()`` keeps the submit button disabled until this field
        carries something - a wrong-password test still has to fill in the
        (correct) recovery key, or it never reaches the password check at
        all.
        """
        self.page.fill("input[autocomplete='current-password']", password)
        self.page.fill("input[spellcheck='false']", secret or self.secret)
        self.page.click("button:has-text('Unlock')")

    def _wait_for_vault_named(self, name, timeout=30000):
        """Wait for the exact vault name, not a substring of it.

        A plain ``text=`` selector is a substring match, and the navbar logo
        renders the literal word "Workspace" on every page - "text=Work"
        resolves to that on load, before the vault list has decrypted
        anything, and the wait returns green whether or not the feature
        works.
        """
        self.page.get_by_text(name, exact=True).wait_for(timeout=timeout)

    def test_onboarding_ends_at_the_unlock_screen_for_the_new_vault(self):
        self._onboard()
        vault = Vault.objects.get(owner=self.user)
        self.assertTrue(
            VaultKeyWrap.objects.filter(vault=vault, recipient=self.user).exists()
        )
        # finish() lands on /vault, not inside it: nothing carries the
        # derived keys across the full navigation, so the account that just
        # sealed its first vault is asked to unlock it like any other visit.
        self.page.wait_for_selector("input[autocomplete='current-password']")

    def test_the_vault_name_never_reaches_the_database_in_the_clear(self):
        self._onboard()
        self.assertNotIn("Personal", Vault.objects.get(owner=self.user).encrypted_name)

    def test_a_reload_asks_for_the_password_again(self):
        self._onboard()
        self.page.reload()
        self.page.wait_for_selector("input[autocomplete='current-password']")

    def test_remembering_the_device_spares_the_recovery_key_on_reload(self):
        self._onboard()
        self.page.reload()
        self.page.check(REMEMBER_CHECKBOX)
        self._unlock()
        self._wait_for_vault_named("Personal", timeout=60000)
        self.page.reload()
        self.page.wait_for_selector("input[autocomplete='current-password']")
        self.assertEqual(self.page.locator("input[spellcheck='false']").count(), 0)

    def test_the_recovery_key_survives_being_typed_one_key_at_a_time(self):
        """Every other test here reaches the field through ``fill()``, which
        delivers the whole key in a single input event - so the value never
        passes through the one-character state, and the field is never asked to
        stay mounted while it holds one. A person types, one keystroke at a
        time, and the field has to still be there at the end of the word."""
        secret = self._onboard()
        self.page.reload()
        self.page.wait_for_selector("input[spellcheck='false']")
        self.page.click("input[spellcheck='false']")
        self.page.keyboard.type(secret)
        self.assertEqual(
            self.page.locator("input[spellcheck='false']").count(),
            1,
            "the recovery-key field must survive its own first character",
        )
        self.assertEqual(self.page.input_value("input[spellcheck='false']"), secret)

    def test_a_wrong_password_fails_without_a_request(self):
        self._onboard()
        self.page.reload()
        requests = []
        self.page.on("request", lambda request: requests.append(request.url))
        self._unlock(password="not the password")
        self.page.wait_for_selector("inline-alert", timeout=30000)
        # The envelope is fetched; nothing else is, and no vault is listed.
        self.assertTrue(
            [url for url in requests if "/account/envelope" in url],
            "the envelope is the one request a failed unlock does make",
        )
        self.assertEqual([url for url in requests if "/api/v1/vault/vaults" in url], [])

    def test_the_right_password_opens_the_vault_and_shows_its_name(self):
        self._onboard()
        self.page.reload()
        self._unlock()
        self._wait_for_vault_named("Personal", timeout=60000)

    def test_locking_takes_the_names_off_the_screen(self):
        self._onboard()
        self.page.reload()
        self._unlock()
        self._wait_for_vault_named("Personal", timeout=60000)
        # Lock lives in the sidebar now, beside the countdown it announces.
        # Not exact: drawer_item renders the countdown as a badge inside the
        # row, so the accessible name carries it too.
        self.page.get_by_role("button", name="Lock").first.click()
        self.page.wait_for_selector("input[autocomplete='current-password']")
        # Not a raw substring check against the whole page: the shared
        # navbar's hidden onboarding tour markup carries the word
        # "Personalize", which contains "Personal" and would make a plain
        # ``assertNotIn`` fail regardless of whether locking actually
        # cleared the vault list.
        self.assertEqual(self.page.get_by_text("Personal", exact=True).count(), 0)

    def test_the_countdown_advances_while_the_vault_stays_unlocked(self):
        # Alpine's reactivity is not observable from a stubbed session in a
        # node:vm sandbox - only a real browser proves the countdown actually
        # changes rather than being rendered once and left stale.
        self._onboard()
        self.page.reload()
        self._unlock()
        self._wait_for_vault_named("Personal", timeout=60000)
        # The badge on the sidebar's Lock row: mm:ss, and nothing else.
        countdown = self.page.locator("span.badge", has_text=":").first
        first = countdown.inner_text()
        self.page.wait_for_timeout(2500)
        second = countdown.inner_text()
        self.assertNotEqual(first, second)

    def test_a_second_vault_can_be_created_and_read_back(self):
        self._onboard()
        self.page.reload()
        self._unlock()
        self._wait_for_vault_named("Personal", timeout=60000)
        self.page.get_by_role("button", name="New vault").click()
        self.page.fill("input[placeholder='Personal, Work…']", "Work")
        self.page.get_by_role("button", name="Create", exact=True).click()
        self._wait_for_vault_named("Work")
        self.page.reload()
        self._unlock()
        self._wait_for_vault_named("Work", timeout=60000)
