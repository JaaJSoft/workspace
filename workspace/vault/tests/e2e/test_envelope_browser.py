"""The account envelope in a real browser: the derivation switch, the
fallback to the previous envelope, and the master password change.

The derivation switch runs under the test bundle with lighter Argon2
parameters than production, so the account onboards under the production
parameters first and unlocks under the switched ones.
"""

from django.contrib.auth import get_user_model
from django.test import override_settings

from workspace.vault.models import AccountIdentity
from workspace.vault.services import suites
from workspace.vault.tests.factories import sealed_envelope

from .test_browser import CORPUS_ROUTE, GOOD_PASSWORD, VaultBrowserCase

LIGHT_PARAMS = {"v": "1.3", "m": 16384, "t": 1, "p": 1}
LIGHT_KDF = {"kdf": {"argon2id": {"params": LIGHT_PARAMS}}}
PREVIOUS_PASSWORD_NOTICE = "text=Unlocked with your previous password"
ENVELOPE_COLUMNS = (
    "kdf_algo",
    "kdf_params",
    "wrapped_kex_priv",
    "wrapped_sig_priv",
    "previous_kdf_algo",
    "previous_kdf_params",
    "previous_wrapped_kex_priv",
    "previous_wrapped_sig_priv",
)
ROTATE_ROUTE = "**/api/v1/vault/account/rotate"


def _envelope(user):
    return AccountIdentity.objects.filter(user=user).values(*ENVELOPE_COLUMNS).get()


class EnvelopeBrowserCase(VaultBrowserCase):
    def _unlock_and_settle(self, page=None):
        page = page or self.page
        page.reload()
        self._unlock_on(page)
        self._wait_unlocked(page)
        page.evaluate("() => window.vaultSession.envelopeWrites()")

    def _unlock_on(self, page, password=GOOD_PASSWORD):
        """Unlock *page*, typing the recovery key only where the device does
        not remember it - a second browser context never does."""
        page.wait_for_selector("input[autocomplete='current-password']")
        page.fill("input[autocomplete='current-password']", password)
        secret = page.locator("input[spellcheck='false']")
        if (
            secret.count()
            and secret.first.is_visible()
            and not secret.first.input_value()
        ):
            secret.first.fill(self.secret)
        page.click("button:has-text('Unlock')")

    def _wait_unlocked(self, page):
        page.wait_for_function(
            "() => window.vaultSession && window.vaultSession.isUnlocked()",
            timeout=60000,
        )

    def _second_page(self):
        """The same account, signed in on another device."""
        context = self.browser.new_context()
        context.add_cookies(self.context.cookies())
        context.route(
            "**/api/v1/stream**", lambda route: route.fulfill(status=204, body="")
        )
        page = context.new_page()
        page.route(
            CORPUS_ROUTE,
            lambda route: route.fulfill(
                status=200, body="0000000000000000000000000000000000000:1\n"
            ),
        )
        self.addCleanup(context.close)
        page.goto(f"{self.live_server_url}/vault")
        return page


@override_settings(VAULT_TEST_MANIFEST=LIGHT_KDF)
class KdfParamsSwitchBrowserTests(EnvelopeBrowserCase):
    def setUp(self):
        super().setUp()
        with override_settings(VAULT_TEST_MANIFEST=None):
            self._open_vault()
            self.production_params = suites.current_kdf()[1]

    def test_nothing_changes_before_an_unlock(self):
        before = _envelope(self.user)
        self.page.reload()
        self.page.wait_for_selector("input[autocomplete='current-password']")
        self.assertEqual(_envelope(self.user), before)

    def test_one_unlock_rewraps_under_the_new_params_and_keeps_the_old_envelope(self):
        before = _envelope(self.user)
        self._unlock_and_settle()
        after = _envelope(self.user)
        self.assertEqual(after["kdf_params"], LIGHT_PARAMS)
        self.assertNotEqual(after["wrapped_kex_priv"], before["wrapped_kex_priv"])
        self.assertEqual(after["previous_kdf_params"], self.production_params)
        self.assertEqual(after["previous_wrapped_kex_priv"], before["wrapped_kex_priv"])
        self.assertEqual(after["previous_wrapped_sig_priv"], before["wrapped_sig_priv"])

    def test_the_next_unlock_confirms_with_the_same_password(self):
        self._unlock_and_settle()
        rewrapped = _envelope(self.user)
        self._unlock_and_settle()
        confirmed = _envelope(self.user)
        self.assertEqual(confirmed["wrapped_kex_priv"], rewrapped["wrapped_kex_priv"])
        self.assertIsNone(confirmed["previous_wrapped_kex_priv"])
        self.assertIsNone(confirmed["previous_kdf_params"])

    def test_a_broken_current_envelope_falls_back_and_writes_nothing(self):
        self._unlock_and_settle()
        AccountIdentity.objects.filter(user=self.user).update(
            wrapped_kex_priv=sealed_envelope("written-by-a-faulty-client")
        )
        broken = _envelope(self.user)
        self._unlock_and_settle()
        self.assertTrue(
            self.page.evaluate("() => window.vaultSession.openedWithPrevious()")
        )
        self.page.wait_for_selector(PREVIOUS_PASSWORD_NOTICE)
        self.assertEqual(_envelope(self.user), broken)

    def test_an_account_that_never_unlocks_stays_byte_for_byte(self):
        """A second identity row: it never opens, so its keys need not be
        real - only that nothing touched it."""
        other = get_user_model().objects.create_user(username="away", password="pw")
        AccountIdentity.objects.create(
            user=other,
            kdf_algo="argon2id",
            kdf_params=self.production_params,
            kdf_salt="U0FMVA",
            kex_public="KEX",
            sig_public="SIG",
            wrapped_kex_priv=sealed_envelope("away-kex", 1),
            wrapped_sig_priv=sealed_envelope("away-sig", 1),
            sig_over_kex_pub="ATTEST",
            state=AccountIdentity.State.ACTIVE,
        )
        away_before = _envelope(other)
        self._unlock_and_settle()
        self.assertEqual(_envelope(self.user)["kdf_params"], LIGHT_PARAMS)
        self.assertEqual(_envelope(other), away_before)

    def test_a_cut_rewrap_leaves_the_envelope_and_the_next_unlock_finishes(self):
        before = _envelope(self.user)
        self.page.route(ROTATE_ROUTE, lambda route: route.abort())
        self._unlock_and_settle()
        self.assertEqual(_envelope(self.user), before)
        self.page.unroute(ROTATE_ROUTE)
        self._unlock_and_settle()
        self.assertEqual(_envelope(self.user)["kdf_params"], LIGHT_PARAMS)


NEW_PASSWORD = "orbit-lantern-pebble-quartz-71"
# The start of the wrong-password reason the unlock screen reports.
WRONG_PASSWORD_MESSAGE = "text=That master password does not open this account"


class PasswordChangeBrowserTests(EnvelopeBrowserCase):
    def setUp(self):
        super().setUp()
        self._open_vault()

    def _fill_dialog(self, current=GOOD_PASSWORD, new=NEW_PASSWORD):
        self.page.click(".drawer-side >> text=Change password")
        self.page.fill("[data-testid=password-current]", current)
        self.page.fill("[data-testid=password-new]", new)
        self.page.fill("[data-testid=password-confirm]", new)
        self.page.wait_for_selector(
            "[data-testid=password-submit]:not([disabled])", timeout=15000
        )

    def _rotate_requests(self):
        seen = []
        self.page.on(
            "request",
            lambda request: (
                seen.append(request.url)
                if request.method == "POST" and request.url.endswith("/account/rotate")
                else None
            ),
        )
        return seen

    def test_the_old_password_works_until_the_new_one_unlocks(self):
        before = _envelope(self.user)
        self._fill_dialog()
        self.page.click("[data-testid=password-submit]")
        self.page.wait_for_selector("text=Password changed", timeout=60000)
        changed = _envelope(self.user)
        self.assertNotEqual(changed["wrapped_kex_priv"], before["wrapped_kex_priv"])
        self.assertEqual(
            changed["previous_wrapped_kex_priv"], before["wrapped_kex_priv"]
        )
        self.assertEqual(
            changed["previous_wrapped_sig_priv"], before["wrapped_sig_priv"]
        )
        self.assertFalse(self.page.evaluate("() => window.vaultSession.isUnlocked()"))

        # Before the confirmation: the old password still opens, from another
        # device, and writes nothing.
        elsewhere = self._second_page()
        self._unlock_on(elsewhere, GOOD_PASSWORD)
        self._wait_unlocked(elsewhere)
        elsewhere.wait_for_selector(PREVIOUS_PASSWORD_NOTICE)
        elsewhere.evaluate("() => window.vaultSession.envelopeWrites()")
        self.assertEqual(_envelope(self.user), changed)

        # The new password opens and confirms.
        self._unlock_on(self.page, NEW_PASSWORD)
        self._wait_unlocked(self.page)
        self.page.evaluate("() => window.vaultSession.envelopeWrites()")
        confirmed = _envelope(self.user)
        self.assertEqual(confirmed["wrapped_kex_priv"], changed["wrapped_kex_priv"])
        self.assertIsNone(confirmed["previous_wrapped_kex_priv"])

        # After it, the old password is only a wrong password.
        later = self._second_page()
        self._unlock_on(later, GOOD_PASSWORD)
        later.wait_for_selector(WRONG_PASSWORD_MESSAGE, timeout=60000)
        self.assertFalse(later.evaluate("() => window.vaultSession.isUnlocked()"))

    def test_a_reseal_that_does_not_reread_sends_nothing(self):
        seen = self._rotate_requests()
        self._fill_dialog()
        # From now on every seal encrypts its plaintext with one bit flipped.
        # The ciphertext is valid and opens - to another key than the one
        # handed in - so only comparing the reread with the original catches
        # it, and that must happen before any request leaves. A flipped
        # ciphertext byte would not do: it fails the open itself.
        self.page.evaluate(
            """() => {
                const real = window.vaultCrypto.seal;
                window.vaultCrypto.seal = (key, plaintext, ...rest) => {
                    const altered = new Uint8Array(plaintext);
                    altered[0] ^= 1;
                    return real(key, altered, ...rest);
                };
            }"""
        )
        before = _envelope(self.user)
        self.page.click("[data-testid=password-submit]")
        # Either outcome ends the wait, so a rotation that did go out is
        # reported as one rather than as a timeout.
        refused = self.page.get_by_text("The new password could not be applied")
        refused.or_(self.page.get_by_text("Password changed")).first.wait_for(
            timeout=60000
        )
        self.assertEqual(seen, [])
        self.assertTrue(refused.is_visible())
        self.assertEqual(_envelope(self.user), before)

    def test_a_wrong_current_password_sends_nothing(self):
        seen = self._rotate_requests()
        before = _envelope(self.user)
        self._fill_dialog(current="not-the-password-at-all")
        self.page.click("[data-testid=password-submit]")
        self.page.wait_for_selector("text=Current password is incorrect", timeout=60000)
        self.assertEqual(seen, [])
        self.assertEqual(_envelope(self.user), before)
