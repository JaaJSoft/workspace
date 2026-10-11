"""E2E: the contact menu draws its own icons, not the whole page's.

Skipped unless E2E=1.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.common.tests.e2e.icons import IconScopeMixin
from workspace.people.services.persons import create_person


class PeopleIconScopeTests(IconScopeMixin, PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="people-icons")
        create_person(owner=self.user, display_name="Bob Martin")
        self.login_as(self.user)

    def test_a_contact_context_menu(self):
        self.page.goto(f"{self.live_server_url}/people")
        row = self.page.locator("#person-list a", has_text="Bob Martin")
        expect(row).to_be_visible()
        self.forget_bystander_redraws()

        row.click(button="right")

        menu = self.page.locator("#people-context-menu")
        expect(
            menu.locator("li")
            .filter(has=self.page.get_by_role("button", name="Delete"))
            .locator("svg[data-lucide]")
        ).to_be_attached()
        self.assert_bystander_kept()
