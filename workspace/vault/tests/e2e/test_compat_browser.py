"""A real browser opens an account this build never wrote.

Task 2 (test_compat_reference.py) and Task 3 (test_compat_server.py) both
prove the corpus, but both share ``cbor2`` between the writer and the
reader - the module's own docs call that agreement partly circular. The
browser bundle carries its own CBOR encoder (``cbor-x``), its own AEAD, its
own everything: it is the only reader here that shares no line with either
half of the corpus's own generation walk. Opening the frozen rows with it is
what closes the circularity.

``READ_EVERYTHING`` is imported from ``compat_scripts`` rather than defined
here: it is the exact script the generation walk used to freeze the
manifest, so comparing its output against that manifest is only meaningful
if the two sides run identical code.
"""

from django.contrib.auth import get_user_model
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from workspace.common.tests.e2e.base import PlaywrightTestCase

from .. import compat
from .compat_scripts import READ_EVERYTHING

# Every published corpus version a replay test in this file reads. Task 5
# checks this against compat.versions() so a new corpus directory can never
# go unread.
COVERED = ["v1"]

CORPUS_ROUTE = "https://api.pwnedpasswords.com/range/*"


class CorpusBrowserReplayTests(PlaywrightTestCase):
    fixtures = [str(compat.load("v1").rows)]

    def setUp(self):
        super().setUp()
        self.corpus = compat.load("v1")
        self.user = get_user_model().objects.get(
            username=self.corpus.credentials["username"]
        )
        self.login_as(self.user)
        # The unlock screen never calls out to pwnedpasswords itself, but
        # onboarding does, and stubbing it unconditionally is what keeps
        # this test from depending on the network regardless of which path
        # a future change routes it through.
        self.page.route(
            CORPUS_ROUTE,
            lambda route: route.fulfill(
                status=200, body="0000000000000000000000000000000000000:1\n"
            ),
        )

    def test_a_browser_opens_every_row_of_the_frozen_account(self):
        """The whole manifest, compared in one shot - never a walk of
        selected keys. A partial comparison would pass on a reader that
        silently dropped a vault, a folder, or a field.
        """
        self.page.goto(f"{self.live_server_url}/vault")
        self.page.wait_for_selector("input[autocomplete='current-password']")
        self.page.fill(
            "input[autocomplete='current-password']",
            self.corpus.credentials["vault_master_password"],
        )
        self.page.fill(
            "input[spellcheck='false']", self.corpus.credentials["secret_key"]
        )
        self.page.click("button:has-text('Unlock')")
        self.page.wait_for_function(
            "() => window.vaultSession && window.vaultSession.isUnlocked()",
            timeout=60000,
        )

        # A failed AEAD open inside READ_EVERYTHING reaches here as a bare
        # WebCrypto ``OperationError`` - correct, but illegible to a reader
        # two years from now with no WebCrypto background: nothing in that
        # name says corpus, decryption, or format. TimeoutError is left to
        # propagate as itself - it means Playwright never got an answer, not
        # that the answer it got failed to decrypt, and relabelling it here
        # would send the reader to the wrong cause. Every other Error raised
        # by the evaluate is re-raised as an AssertionError that names what
        # actually happened, chained so the original OperationError and its
        # stack stay visible.
        try:
            read = self.page.evaluate(READ_EVERYTHING)
        except PlaywrightTimeoutError:
            raise
        except PlaywrightError as exc:
            raise AssertionError(
                "The frozen v1 corpus no longer opens with today's vault "
                "bundle - a format or algorithm change has broken "
                f"compatibility with data already written. {exc}"
            ) from exc

        self.assertEqual(read, self.corpus.manifest)
