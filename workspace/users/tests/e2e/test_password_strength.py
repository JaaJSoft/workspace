"""The strength meter under the new-password field on the settings page.

The unit tests pin what the component says for a given score; what only a
browser can show is that the estimator is fetched when the security section
opens and not before, and that typing into the real field drives the meter.
"""

from __future__ import annotations

import re

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

BUNDLE = "password-strength/password-strength.js"
NEW_PASSWORD = "input[autocomplete='new-password'] >> nth=0"
METER = "[data-password-strength]"


class PasswordStrengthMeterTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="meter")
        self.login_as(self.user)
        self.bundle_requests = []
        self.page.on(
            "request",
            lambda request: (
                BUNDLE in request.url and self.bundle_requests.append(request.url)
            ),
        )

    def test_the_estimator_loads_with_the_security_section_and_not_before(self):
        self.page.goto(f"{self.live_server_url}/users/settings")
        self.page.wait_for_load_state("networkidle")
        self.assertEqual(
            self.bundle_requests, [], "the estimator was fetched on first paint"
        )

        self.page.click("button:has-text('Security')")
        self.page.wait_for_load_state("networkidle")
        self.assertEqual(len(self.bundle_requests), 1)

    def test_a_common_password_is_called_out(self):
        self.page.goto(f"{self.live_server_url}/users/settings#security")
        self.page.wait_for_selector(NEW_PASSWORD)
        self.page.fill(NEW_PASSWORD, "password123")

        meter = self.page.locator(METER)
        expect(meter).to_be_visible()
        expect(meter).to_contain_text(re.compile(r"Very weak|Weak"))
        expect(meter).to_contain_text(re.compile(r"password", re.I))
        # The sentence, not the key zxcvbn reports without translations.
        expect(meter).not_to_contain_text(re.compile(r"\btopTen\b|\bcommon\b(?! )"))
        expect(meter.locator("progress")).to_have_class(re.compile(r"progress-error"))

    def test_a_strong_password_earns_the_top_band(self):
        self.page.goto(f"{self.live_server_url}/users/settings#security")
        self.page.wait_for_selector(NEW_PASSWORD)
        self.page.fill(NEW_PASSWORD, "correct-horse-battery-staple-42")

        meter = self.page.locator(METER)
        expect(meter).to_contain_text("Very strong")
        expect(meter.locator("progress")).to_have_class(re.compile(r"progress-success"))

    def test_clearing_the_field_hides_the_meter(self):
        self.page.goto(f"{self.live_server_url}/users/settings#security")
        self.page.wait_for_selector(NEW_PASSWORD)
        self.page.fill(NEW_PASSWORD, "password123")
        meter = self.page.locator(METER)
        expect(meter).to_be_visible()

        self.page.fill(NEW_PASSWORD, "")
        expect(meter).to_be_hidden()
