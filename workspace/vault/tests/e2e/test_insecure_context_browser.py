"""The vault pages on an instance served over plain HTTP.

The browser withholds ``crypto.subtle`` on any plain-HTTP origin other than
localhost, so the onboarding cannot seal an identity and the unlock screen
cannot open one. Left alone, both fail on the first click with an error that
invites a retry which can never succeed. These tests pin the page saying why
instead, and offering nothing to type into.

Only localhost and loopback literals count as trustworthy, so an ordinary
hostname mapped onto the live server produces a genuinely insecure origin.
"""

from django.conf import settings
from django.core.cache import cache
from django.test import Client, override_settings

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.vault.models import AccountIdentity

INSECURE_HOST = "vault-insecure.test"
NOTICE = "The vault needs a secure connection"


@override_settings(ALLOWED_HOSTS=["*"])
class InsecureContextTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="owner", email="owner@example.com")
        port = self.live_server_url.rsplit(":", 1)[1]
        self.insecure_url = f"http://{INSECURE_HOST}:{port}"
        self.insecure_browser = self._playwright.chromium.launch(
            headless=self.HEADLESS,
            args=[f"--host-resolver-rules=MAP {INSECURE_HOST} 127.0.0.1"],
        )
        context = self.insecure_browser.new_context()
        client = Client()
        client.force_login(self.user)
        context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": client.cookies[settings.SESSION_COOKIE_NAME].value,
                    "url": self.insecure_url,
                }
            ]
        )
        self.insecure_page = context.new_page()

    def tearDown(self):
        self.insecure_browser.close()
        cache.clear()
        super().tearDown()

    def _open(self, path):
        page = self.insecure_page
        page.goto(self.insecure_url + path)
        self.assertFalse(page.evaluate("window.isSecureContext"), "expected a non-secure origin")
        page.get_by_text(NOTICE).wait_for()
        return page

    def test_the_onboarding_says_https_is_required_and_offers_no_step(self):
        page = self._open("/vault/onboarding")

        self.assertFalse(page.get_by_role("button", name="I understand").is_visible())

    def test_the_unlock_screen_says_https_is_required_and_offers_no_field(self):
        AccountIdentity.objects.create(
            user=self.user,
            kdf_params={"m": 65536, "t": 3, "p": 2},
            kdf_salt="SALT",
            kex_public="KEX",
            sig_public="SIG",
            wrapped_kex_priv="WKEX",
            wrapped_sig_priv="WSIG",
            sig_over_kex_pub="ATTEST",
            state=AccountIdentity.State.ACTIVE,
        )

        page = self._open("/vault")

        self.assertEqual(page.locator("input[autocomplete='current-password']").count(), 0)

    def test_a_secure_origin_shows_no_notice(self):
        """The guard reads the browser's verdict, not the scheme: localhost
        over plain HTTP is a secure context and keeps the onboarding."""
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/vault/onboarding")
        self.page.get_by_role("button", name="I understand").wait_for()

        self.assertFalse(self.page.get_by_text(NOTICE).is_visible())
