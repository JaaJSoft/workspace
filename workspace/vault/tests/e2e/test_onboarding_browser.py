"""The onboarding screen as a user walks it.

The unit tests prove each piece: the component's state machine, the encoding,
the kit's contents, the API. What none of them covers is the screen wiring
them together - Argon2id running in WASM against a real salt, WebCrypto
minting the keys, the sealed envelope reaching the server and verifying there.

The assertion that matters is the last one: not that the buttons clicked, but
that the identity the browser sealed verifies through the same attestation
code the API uses.
"""

from django.core.cache import cache
from django.urls import reverse

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.vault.models import AccountIdentity
from workspace.vault.services.attestation import verify_kex_pub_attestation

# Long, unguessable, and not in any corpus - the floor has to pass on it.
GOOD_PASSWORD = "correct-horse-battery-staple-42"

# The corpus is a third party. Answering it ourselves keeps the suite from
# going red on someone else's outage, and lets the "not found" branch be the
# one under test rather than an accident of the day.
CORPUS_ROUTE = "https://api.pwnedpasswords.com/range/*"


class OnboardingWalkTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        # create_user, not User.objects.create_user: the shared navbar opens
        # the welcome tour and the changelog on a fresh account, and their
        # backdrops swallow every click on the page underneath.
        self.user = self.create_user(username="owner", email="owner@example.com")
        self.login_as(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _serve_corpus(self, body="0000000000000000000000000000000000000:1\n"):
        self.page.route(
            CORPUS_ROUTE,
            lambda route: route.fulfill(status=200, body=body),
        )

    def _walk_to_the_password_step(self):
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.wait_for_url("**/vault/onboarding")
        # The steps are x-if blocks: nothing exists until Alpine has booted,
        # so waiting for the button is waiting for the component.
        self.page.wait_for_selector("button:has-text('I understand')")
        self.page.click("button:has-text('I understand')")
        self.page.wait_for_selector("input[autocomplete='new-password']")

    def _fill_password(self, password=None):
        password = password or GOOD_PASSWORD
        self.page.fill("input[autocomplete='new-password'] >> nth=0", password)
        self.page.fill("input[autocomplete='new-password'] >> nth=1", password)

    def _seal(self):
        """Fill, wait for the floor to clear, and let the browser seal.

        zxcvbn is asynchronous and Argon2id at 64 MiB is not instant - both by
        design - so this waits on the state rather than on a duration.
        """
        self._fill_password()
        self.page.wait_for_selector(
            "button:has-text('Create my vault'):not([disabled])", timeout=15000
        )
        self.page.click("button:has-text('Create my vault')")
        self.page.wait_for_selector("text=Your recovery key", timeout=60000)

    def test_a_user_without_an_identity_lands_on_onboarding(self):
        self.page.goto(f"{self.live_server_url}/vault")
        self.assertIn("/vault/onboarding", self.page.url)

    def test_a_short_password_cannot_advance(self):
        self._serve_corpus()
        self._walk_to_the_password_step()
        self._fill_password("short")
        self.page.wait_for_timeout(1500)
        self.assertTrue(self.page.is_disabled("button:has-text('Create my vault')"))

    def test_the_whole_flow_seals_an_identity_the_server_verifies(self):
        self._serve_corpus()
        self._walk_to_the_password_step()
        self._seal()

        identity = AccountIdentity.objects.get(user=self.user)
        self.assertEqual(identity.state, AccountIdentity.State.ACTIVE)
        verify_kex_pub_attestation(
            identity.uuid,
            identity.kex_public,
            identity.sig_public,
            identity.sig_over_kex_pub,
        )

    def test_the_last_step_cannot_be_left_unacknowledged(self):
        self._serve_corpus()
        self._walk_to_the_password_step()
        self._seal()

        opener = self.page.locator("a:has-text('Open my vault')")
        # On the href, not the class: btn-disabled compiles to pointer-events
        # alone, which leaves the link in the tab order for Enter to follow -
        # so a class assertion passes on a gate anyone can walk around.
        self.assertIsNone(opener.get_attribute("href"))
        self.assertEqual(opener.get_attribute("aria-disabled"), "true")
        self.assertIn("btn-disabled", opener.get_attribute("class"))

        # By id, not by type: the shared layout's drawer toggle is a checkbox
        # too, and it comes first in the document.
        self.page.check("#recovery-key-acknowledged")
        self.assertEqual(opener.get_attribute("href"), reverse("vault_ui:index"))
        self.assertNotIn("btn-disabled", opener.get_attribute("class"))

    def test_a_recovery_key_is_shown_grouped_for_transcription(self):
        self._serve_corpus()
        self._walk_to_the_password_step()
        self._seal()

        shown = self.page.inner_text("pre")
        self.assertIn("-", shown)
        # The body only: the trailing check symbol is drawn from a wider
        # alphabet that includes U, so asserting on the whole string fails on
        # roughly one freshly minted secret in 37.
        body = shown.replace("-", "")[:-1]
        # The alphabet is what makes a hand-copied secret survive; letters it
        # excludes must never appear.
        for confusable in ("I", "L", "O", "U"):
            self.assertNotIn(confusable, body)

    def test_the_way_out_closes_only_once_the_key_exists(self):
        """The lock belongs to the recovery key, not to the flow. Before the
        seal nothing is at stake and trapping the user would be gratuitous;
        after it, the key lives on this screen alone and Escape would take it
        with no way to ask for it again."""
        dialog = "#vault-onboarding-dialog"
        self._serve_corpus()
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.wait_for_url("**/vault/onboarding")
        self.page.wait_for_selector(f"{dialog}[open]")

        # No control offers to close it either way: the exits a dialog has and
        # a page does not are the ones this flow cannot afford.
        self.assertEqual(self.page.locator(f"{dialog} [aria-label='Close']").count(), 0)
        self.assertEqual(
            self.page.locator(f"{dialog} form[method='dialog']").count(), 0
        )

        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(200)
        self.assertFalse(
            self.page.evaluate(f"document.querySelector('{dialog}').open"),
            "nothing is at stake yet, so the user may leave",
        )

        self.page.goto(f"{self.live_server_url}/vault/onboarding")
        self._walk_to_the_password_step()
        self._seal()
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(200)
        self.assertTrue(
            self.page.evaluate(f"document.querySelector('{dialog}').open"),
            "the recovery key is on screen and nowhere else",
        )

    def test_each_step_hands_the_keyboard_somewhere(self):
        """A modal makes the page behind inert, so focus falling to the body
        is focus lost: the step's controls become unreachable by keyboard on a
        flow that cannot be restarted."""
        self._serve_corpus()
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.wait_for_url("**/vault/onboarding")
        self.page.wait_for_selector("button:has-text('I understand')")
        self.page.click("button:has-text('I understand')")
        self.page.wait_for_selector("input[autocomplete='new-password']")
        self.assertEqual(
            self.page.evaluate("document.activeElement.tagName.toLowerCase()"), "input"
        )

        self._fill_password()
        self.page.wait_for_selector(
            "button:has-text('Create my vault'):not([disabled])"
        )
        self.page.click("button:has-text('Create my vault')")
        self.page.wait_for_selector("#recovery-key-acknowledged")
        self.assertEqual(
            self.page.evaluate("document.activeElement.tagName.toLowerCase()"), "pre"
        )

    def test_the_warning_stays_on_screen_through_every_step(self):
        """It used to live on the first step alone, which is the one people
        click past - and it is the only thing telling them the key they are
        about to be shown is the only copy there will ever be."""
        warning = "text=There is no password reset"
        self._serve_corpus()
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.wait_for_url("**/vault/onboarding")
        self.page.wait_for_selector("button:has-text('I understand')")
        self.assertTrue(self.page.is_visible(warning), "step 1")

        self.page.click("button:has-text('I understand')")
        self.page.wait_for_selector("input[autocomplete='new-password']")
        self.assertTrue(self.page.is_visible(warning), "step 2")

        self._seal()
        self.assertTrue(self.page.is_visible(warning), "step 3")

    def test_an_unreachable_corpus_warns_without_blocking(self):
        """A third party that is down must not be able to stop someone
        protecting their vault."""
        self.page.route(CORPUS_ROUTE, lambda route: route.abort())
        self._walk_to_the_password_step()
        self._fill_password()
        self.page.wait_for_selector(
            "button:has-text('Create my vault'):not([disabled])", timeout=15000
        )
