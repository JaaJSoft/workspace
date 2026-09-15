"""What the browser actually refuses on the module's pages.

``test_csp.py`` asserts the header goes out. It cannot tell whether the page
under that header still works: a policy is only ever proved by the browser
that enforces it. This walks both vault pages in Chromium and fails on any
``securitypolicyviolation`` the document reports.

It is the guard on the shared chrome, not on the module: the vault extends
``base.html`` like every other page, so an inline ``<script>``, a ``style=``
attribute or a CDN tag added to the navbar tomorrow lands here as a blank
banner - and this test names the offender instead.
"""

import time

from django.core.cache import cache

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.vault.models import AccountIdentity

# Collected in the page, not from the console: a violation event carries the
# blocked URI and the directive that refused it, which is what tells you which
# tag to go and fix.
COLLECT_VIOLATIONS = """
window.__cspViolations = [];
document.addEventListener('securitypolicyviolation', (e) => {
  window.__cspViolations.push({
    directive: e.effectiveDirective || e.violatedDirective,
    blocked: e.blockedURI,
    sample: e.sample || '',
    line: e.lineNumber,
    source: e.sourceFile || '',
  });
});
"""

# An external image is the smallest violation that needs no network: img-src
# refuses it before any request leaves the browser. The query string must not
# survive into the log.
PROVOKE_VIOLATION = """
const img = document.createElement('img');
img.src = 'https://blocked.example/pixel.png?leak=1';
document.body.appendChild(img);
"""


class VaultCspEnforcementTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="owner", email="owner@example.com")
        self.login_as(self.user)
        self.page.add_init_script(COLLECT_VIOLATIONS)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _violations_on(self, path):
        self.page.goto(f"{self.live_server_url}{path}")
        # The navbar's icons are drawn on DOMContentLoaded and Alpine boots
        # after it; a violation raised by either would be missed by a load
        # that only waits for the document.
        self.page.wait_for_load_state("networkidle")
        return self.page.evaluate("window.__cspViolations")

    def _assert_clean(self, path):
        violations = self._violations_on(path)
        self.assertEqual(
            violations,
            [],
            "\n".join(
                f"{v['directive']} refused {v['blocked']} "
                f"({v['source']}:{v['line']}) {v['sample']}"
                for v in violations
            ),
        )

    def test_onboarding_runs_without_a_single_refusal(self):
        self._assert_clean("/vault/onboarding")

    def test_the_vault_page_runs_without_a_single_refusal(self):
        AccountIdentity.objects.create(
            user=self.user, kdf_salt="SALT", state=AccountIdentity.State.ACTIVE
        )
        self._assert_clean("/vault")

    def test_a_refusal_reaches_the_report_endpoint(self):
        """The header and the endpoint are each tested on their own; only a
        browser proves they meet. It also proves the request survives what a
        real one carries - the signed-in page's cookies, and no CSRF token."""
        with self.assertLogs("workspace.core.csp_report", "WARNING") as logs:
            self.page.goto(f"{self.live_server_url}/vault/onboarding")
            self.page.wait_for_load_state("networkidle")
            self.page.evaluate(PROVOKE_VIOLATION)
            # The report is posted by the browser on its own schedule and
            # handled on the live server's thread.
            deadline = time.monotonic() + 10
            while not logs.records and time.monotonic() < deadline:
                time.sleep(0.1)

        lines = [record.getMessage() for record in logs.records]
        self.assertTrue(
            any(
                "img-src" in line and "https://blocked.example/pixel.png" in line
                for line in lines
            ),
            lines,
        )
        self.assertFalse([line for line in lines if "leak=1" in line], lines)

    def test_the_shared_navbar_is_on_the_page(self):
        """The whole point of the layout change: the module is not a walled
        garden with its own header. If this stops rendering, the CSP tests
        above would go green on a page that lost its chrome."""
        self.page.goto(f"{self.live_server_url}/vault/onboarding")
        self.page.wait_for_selector(".navbar")
        # The hidden logout form only renders for an authenticated user, so
        # its presence is what says the real chrome rendered rather than a
        # signed-out shell.
        self.page.wait_for_selector("#logout-form", state="attached")
