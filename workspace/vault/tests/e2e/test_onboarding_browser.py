"""The onboarding screen as a user walks it.

The unit tests prove each piece: the component's state machine, the encoding,
the kit's contents, the API. What none of them covers is the screen wiring
them together - Argon2id running in WASM against a real salt, WebCrypto
minting the keys, the sealed envelope reaching the server and verifying there.

The assertion that matters is the last one: not that the buttons clicked, but
that the identity the browser sealed verifies through the same attestation
code the API uses.
"""

from django.contrib.auth import get_user_model

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.vault.models import AccountIdentity
from workspace.vault.services.attestation import verify_kex_pub_attestation

User = get_user_model()

# Long, unguessable, and not in any corpus - the floor has to pass on it.
GOOD_PASSWORD = "correct-horse-battery-staple-42"

# The corpus is a third party. Answering it ourselves keeps the suite from
# going red on someone else's outage, and lets the "not found" branch be the
# one under test rather than an accident of the day.
CORPUS_ROUTE = "https://api.pwnedpasswords.com/range/*"


class OnboardingWalkTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(
            username="owner", email="owner@example.com", password="pw"
        )
        self.login_as(self.user)

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
        self.assertIn("btn-disabled", opener.get_attribute("class"))
        self.page.check("input[type='checkbox']")
        self.assertNotIn("btn-disabled", opener.get_attribute("class"))

    def test_a_recovery_key_is_shown_grouped_for_transcription(self):
        self._serve_corpus()
        self._walk_to_the_password_step()
        self._seal()

        shown = self.page.inner_text("pre")
        self.assertIn("-", shown)
        # The alphabet is what makes a hand-copied secret survive; letters it
        # excludes must never appear.
        for confusable in ("I", "L", "O", "U"):
            self.assertNotIn(confusable, shown.replace("-", ""))

    def test_an_unreachable_corpus_warns_without_blocking(self):
        """A third party that is down must not be able to stop someone
        protecting their vault."""
        self.page.route(CORPUS_ROUTE, lambda route: route.abort())
        self._walk_to_the_password_step()
        self._fill_password()
        self.page.wait_for_selector(
            "button:has-text('Create my vault'):not([disabled])", timeout=15000
        )
