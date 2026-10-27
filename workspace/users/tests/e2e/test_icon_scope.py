"""E2E: creating an API token draws no icon outside its own dialog.

Skipped unless E2E=1.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.common.tests.e2e.icons import IconScopeMixin


class ApiTokenIconScopeTests(IconScopeMixin, PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="token-icons")
        self.login_as(self.user)

    def test_the_created_token_dialog(self):
        self.page.goto(f"{self.live_server_url}/users/settings#tokens")
        self.page.get_by_role("button", name="New Token").click()
        self.forget_bystander_redraws()

        self.page.get_by_placeholder("e.g. CI script, My bot").fill("CI")
        self.page.get_by_role("button", name="Create token", exact=True).click()

        created = self.page.get_by_role("dialog").filter(has_text="Token Created")
        expect(created).to_be_visible()
        expect(created.locator('svg[data-lucide="copy"]')).to_be_attached()
        self.assert_bystander_kept()
