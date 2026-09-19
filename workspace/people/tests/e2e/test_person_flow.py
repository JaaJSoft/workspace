"""Create a contact, edit it inline, add it to a list, delete it."""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.people.models import Person
from workspace.people.services.lists import create_list


class PersonFlowTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.family = create_list(owner=self.user, name="Family")
        self.login_as(self.user)
        self.page.set_viewport_size({"width": 1280, "height": 900})

    def test_create_edit_list_delete(self):
        self.page.goto(f"{self.live_server_url}/people")
        self.page.get_by_role("button", name="New contact").click()
        self.page.locator("#app-dialog-prompt-input").fill("Bob Martin")
        self.page.locator("#app-dialog-prompt-ok").click()

        panel = self.page.locator("#person-panel")
        expect(panel.get_by_placeholder("Name")).to_have_value("Bob Martin")
        expect(self.page.locator("#person-list")).to_contain_text("Bob Martin")

        panel.get_by_placeholder("Name").fill("Robert Martin")
        panel.get_by_placeholder("Name").press("Tab")
        expect(self.page.locator("#person-list")).to_contain_text("Robert Martin")

        panel.get_by_role("button", name="Add to list").click()
        self.page.locator("#app-dialog-select-input").select_option(
            str(self.family.uuid)
        )
        self.page.locator("#app-dialog-select-ok").click()
        expect(panel).to_contain_text("Family")

        panel.get_by_title("Actions").click()
        panel.get_by_text("Delete").click()
        self.page.locator("#app-dialog-confirm-ok").click()
        expect(self.page.locator("#person-list")).not_to_contain_text("Robert Martin")
        self.assertFalse(Person.objects.filter(owner=self.user).exists())
