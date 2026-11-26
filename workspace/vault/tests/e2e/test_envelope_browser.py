"""The account envelope in a real browser: the derivation switch, the
fallback to the previous envelope, the master password change and the
recovery key replacement.

The derivation switch runs under the test bundle with lighter Argon2
parameters than production, so the account onboards under the production
parameters first and unlocks under the switched ones.
"""

import re

from django.contrib.auth import get_user_model
from django.test import override_settings

from workspace.common.tests.e2e.base import stub_global_sse
from workspace.vault.models import AccountIdentity
from workspace.vault.services import suites
from workspace.vault.tests.factories import sealed_envelope

from .test_browser import CORPUS_ROUTE, GOOD_PASSWORD, SIDEBAR, VaultBrowserCase

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

    def _second_page(self):
        """The same account, signed in on another device."""
        context = self.browser.new_context()
        context.add_cookies(self.context.cookies())
        stub_global_sse(context)
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


RECOVERY_KEY = "[data-recovery-key]"
KEY_REPLACED_NOTICE = "text=Recovery key replaced"
KEY_DISCARDED_NOTICE = "[data-testid=recovery-discarded]"


class RecoveryKeyBrowserTests(EnvelopeBrowserCase):
    """The device remembers the key onboarding drew (``_onboard`` ticks the
    box), so the dialog asks for the master password alone unless a test
    makes the device forget."""

    def setUp(self):
        super().setUp()
        self._open_vault()

    def _open_dialog(self):
        # By role and whole name: the Preferences panel of the same sidebar
        # has a paragraph about the recovery key, and a text match finds it
        # first. The second name is the row's tooltip, on the collapsed rail.
        self.page.locator(SIDEBAR).get_by_role(
            "button", name=re.compile(r"^(?:Recovery key|Replace your recovery key)$")
        ).click()
        self.page.wait_for_selector("[data-testid=recovery-password]")

    def _draw_a_new_key(self, password=GOOD_PASSWORD):
        """Walk the first step and return the key the second one shows."""
        self._open_dialog()
        self.page.fill("[data-testid=recovery-password]", password)
        self.page.click("[data-testid=recovery-prepare]")
        self.page.wait_for_selector(RECOVERY_KEY, timeout=60000)
        return self.page.inner_text(RECOVERY_KEY)

    def _confirm(self):
        self.page.check("[data-testid=recovery-acknowledged]")
        self.page.click("[data-testid=recovery-replace]")

    def _remembered(self, page=None):
        return (page or self.page).evaluate(
            "() => window.vaultSession.rememberedSecret()"
        )

    def _unlock_with(self, page, secret, password=GOOD_PASSWORD):
        """Unlock *page* with a key typed by hand, whatever the field held."""
        page.wait_for_selector("input[autocomplete='current-password']")
        page.fill("input[autocomplete='current-password']", password)
        page.fill("input[spellcheck='false']", secret)
        page.click("button:has-text('Unlock')")

    def test_the_old_key_works_until_the_new_one_unlocks(self):
        before = _envelope(self.user)
        seen = self._rotate_requests()
        new_secret = self._draw_a_new_key()
        self.assertNotEqual(new_secret, self.secret)
        # The step that held the focus is gone; the key takes it, inside the
        # dialog, rather than leaving it on the page behind.
        self.page.wait_for_function(
            "() => document.activeElement.matches('[data-recovery-key]')"
        )

        # The key is on screen and its kit can be saved, and nothing has been
        # sent: a page lost here must leave the account as it was.
        with self.page.expect_download(timeout=60000) as download:
            self.page.click("[data-testid=recovery-kit]")
        self.assertEqual(download.value.suggested_filename, "vault-emergency-kit.pdf")
        self.assertIn(new_secret.encode(), download.value.path().read_bytes())
        self.assertTrue(self.page.is_disabled("[data-testid=recovery-replace]"))
        self.assertEqual(seen, [])
        self.assertEqual(_envelope(self.user), before)

        self._confirm()
        self.page.wait_for_selector(KEY_REPLACED_NOTICE, timeout=60000)
        replaced = _envelope(self.user)
        self.assertNotEqual(replaced["wrapped_kex_priv"], before["wrapped_kex_priv"])
        self.assertEqual(
            replaced["previous_wrapped_kex_priv"], before["wrapped_kex_priv"]
        )
        self.assertEqual(
            replaced["previous_wrapped_sig_priv"], before["wrapped_sig_priv"]
        )
        self.assertFalse(self.page.evaluate("() => window.vaultSession.isUnlocked()"))
        self.assertEqual(self._remembered(), new_secret)

        # Before the confirmation: the old key still opens, from another
        # device, and writes nothing.
        elsewhere = self._second_page()
        self._unlock_on(elsewhere)
        self._wait_unlocked(elsewhere)
        elsewhere.wait_for_selector(PREVIOUS_PASSWORD_NOTICE)
        elsewhere.evaluate("() => window.vaultSession.envelopeWrites()")
        self.assertEqual(_envelope(self.user), replaced)

        # The new key opens, from the device that now remembers it, and
        # confirms.
        self._unlock_on(self.page)
        self._wait_unlocked(self.page)
        self.page.evaluate("() => window.vaultSession.envelopeWrites()")
        confirmed = _envelope(self.user)
        self.assertEqual(confirmed["wrapped_kex_priv"], replaced["wrapped_kex_priv"])
        self.assertIsNone(confirmed["previous_wrapped_kex_priv"])

        # After it, the old key opens nothing, and the new one typed from the
        # kit opens the account on a device that never saw it.
        later = self._second_page()
        self._unlock_on(later)
        later.wait_for_selector(WRONG_PASSWORD_MESSAGE, timeout=60000)
        self.assertFalse(later.evaluate("() => window.vaultSession.isUnlocked()"))
        self._unlock_with(later, new_secret)
        self._wait_unlocked(later)

    def test_a_device_told_not_to_remember_asks_for_the_new_key(self):
        new_secret = self._draw_a_new_key()
        self.page.uncheck("[data-testid=recovery-remember]")
        self._confirm()
        self.page.wait_for_selector(KEY_REPLACED_NOTICE, timeout=60000)
        self.assertIsNone(self._remembered())

        # The gate asks for the key, empty: the one this page loaded with is
        # neither offered nor used.
        field = self.page.locator("input[spellcheck='false']")
        field.wait_for()
        self.assertEqual(field.input_value(), "")
        self._unlock_with(self.page, new_secret)
        self._wait_unlocked(self.page)
        self.page.evaluate("() => window.vaultSession.envelopeWrites()")
        self.assertIsNone(_envelope(self.user)["previous_wrapped_kex_priv"])

    def test_a_device_that_forgot_the_key_is_asked_for_the_current_one(self):
        seen = self._rotate_requests()
        before = _envelope(self.user)
        self.page.evaluate("() => window.vaultSession.forgetDevice()")
        self._open_dialog()
        self.page.fill("[data-testid=recovery-password]", GOOD_PASSWORD)

        # A well-formed key that is not this account's: the check symbol
        # passes, the envelope does not open, and no key is drawn.
        other_key = self.page.evaluate(
            "() => window.vaultCrypto.crockfordEncode(new Uint8Array(32).fill(7))"
        )
        self.page.fill("[data-testid=recovery-secret]", other_key)
        self.page.click("[data-testid=recovery-prepare]")
        self.page.wait_for_selector(
            "text=Master password or recovery key is incorrect", timeout=60000
        )
        self.assertEqual(self.page.locator(RECOVERY_KEY).count(), 0)

        self.page.fill("[data-testid=recovery-secret]", self.secret)
        self.page.click("[data-testid=recovery-prepare]")
        self.page.wait_for_selector(RECOVERY_KEY, timeout=60000)
        # The device did not remember the old key, so the new one is not
        # remembered behind its owner's back either.
        self.assertFalse(self.page.is_checked("[data-testid=recovery-remember]"))
        self.assertEqual(seen, [])
        self.assertEqual(_envelope(self.user), before)

    def test_a_wrong_password_shows_no_key_and_sends_nothing(self):
        seen = self._rotate_requests()
        before = _envelope(self.user)
        self._open_dialog()
        self.page.fill("[data-testid=recovery-password]", "not-the-password-at-all")
        self.page.click("[data-testid=recovery-prepare]")
        self.page.wait_for_selector("text=Master password is incorrect", timeout=60000)
        self.assertEqual(self.page.locator(RECOVERY_KEY).count(), 0)
        self.assertEqual(seen, [])
        self.assertEqual(_envelope(self.user), before)

    def test_cancelling_once_the_key_is_shown_changes_nothing_and_says_so(self):
        seen = self._rotate_requests()
        before = _envelope(self.user)
        self._draw_a_new_key()
        self.page.click("[data-testid=recovery-cancel]")
        self.page.wait_for_selector(KEY_DISCARDED_NOTICE)
        self.assertEqual(seen, [])
        self.assertEqual(_envelope(self.user), before)
        self.assertEqual(self._remembered(), self.secret)

    def test_a_lock_once_the_key_is_shown_changes_nothing_and_says_so(self):
        """The vault locks when its tab is hidden, which is where saving a
        kit can take a phone: the dialog goes without a word of its own."""
        seen = self._rotate_requests()
        before = _envelope(self.user)
        self._draw_a_new_key()
        self.page.evaluate("() => window.vaultSession.lock()")
        self.page.wait_for_selector(KEY_DISCARDED_NOTICE)
        self.assertEqual(self.page.locator(RECOVERY_KEY).count(), 0)
        self.assertEqual(seen, [])
        self.assertEqual(_envelope(self.user), before)

        # The old key still opens, and the notice has had its say.
        self._unlock_on(self.page)
        self._wait_unlocked(self.page)
        self.assertEqual(self.page.locator(KEY_DISCARDED_NOTICE).count(), 0)

    def test_a_conflict_keeps_the_old_key_and_says_why(self):
        before = _envelope(self.user)
        self._draw_a_new_key()
        self.page.route(ROTATE_ROUTE, lambda route: route.fulfill(status=409))
        self._confirm()
        self.page.wait_for_selector(
            "text=so nothing was replaced: the recovery key you had still works",
            timeout=60000,
        )
        # The key that was shown is void, so it leaves the screen.
        self.assertEqual(self.page.locator(RECOVERY_KEY).count(), 0)
        self.assertTrue(self.page.evaluate("() => window.vaultSession.isUnlocked()"))
        self.assertEqual(_envelope(self.user), before)
        self.assertEqual(self._remembered(), self.secret)

        self._unlock_and_settle()
        self.assertEqual(_envelope(self.user), before)

    def test_a_rotation_whose_answer_was_lost_is_still_a_replacement(self):
        """The server applied it and the page never heard: calling the key
        void then would have its owner discard the one that opens the
        account."""
        before = _envelope(self.user)
        new_secret = self._draw_a_new_key()

        def lose_the_answer(route):
            route.fetch()
            route.abort()

        self.page.route(ROTATE_ROUTE, lose_the_answer)
        self._confirm()
        self.page.wait_for_selector(KEY_REPLACED_NOTICE, timeout=60000)
        self.assertNotEqual(
            _envelope(self.user)["wrapped_kex_priv"], before["wrapped_kex_priv"]
        )
        self.assertEqual(self._remembered(), new_secret)
