"""Create a contact, edit it inline, add it to a list, delete it."""

from __future__ import annotations

from django.contrib.auth.models import Group
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
        dialog = self.page.locator("#people-person-dialog")
        expect(dialog.locator('select[name="scope"]')).to_have_value("mine")
        dialog.locator('input[name="display_name"]').fill("Bob Martin")
        dialog.locator('input[name="email"]').fill("bob@example.com")
        dialog.get_by_role("button", name="Create").click()

        panel = self.page.locator("#person-panel")
        expect(panel.get_by_placeholder("Name")).to_have_value("Bob Martin")
        expect(panel.locator('input[x-model="entry.value"]').first).to_have_value(
            "bob@example.com"
        )
        expect(self.page.locator("#person-list")).to_contain_text("Bob Martin")

        # Right-click on a row opens the same menu the panel button does, filled
        # from the registry: the row must offer what the server allows.
        self.page.locator("#person-list a", has_text="Bob Martin").click(button="right")
        menu = self.page.locator("#people-context-menu")
        expect(menu).to_be_visible()
        expect(menu).to_contain_text("Delete")
        self.page.keyboard.press("Escape")
        expect(menu).to_be_hidden()

        panel.get_by_placeholder("Name").fill("Robert Martin")
        panel.get_by_placeholder("Name").press("Tab")
        expect(self.page.locator("#person-list")).to_contain_text("Robert Martin")

        panel.get_by_role("button", name="Add to list").click()
        pick = self.page.locator("#people-list-pick-dialog")
        expect(pick).to_contain_text("Robert Martin")
        pick.locator('select[name="list"]').select_option(str(self.family.uuid))
        pick.get_by_role("button", name="Add").click()
        expect(panel).to_contain_text("Family")

        panel.get_by_title("Actions").click()
        # By role: the hidden list menu holds a "Delete" row of its own.
        self.page.locator("#people-context-menu").get_by_role(
            "button", name="Delete"
        ).click()
        self.page.locator("#app-dialog-confirm-ok").click()
        expect(self.page.locator("#person-list")).not_to_contain_text("Robert Martin")
        self.assertFalse(Person.objects.filter(owner=self.user).exists())

    def test_a_group_list_is_marked_in_the_sidebar(self):
        # Two scopes can name a list the same way and the sidebar mixes them:
        # the icon is the only thing telling a group's list from a personal one.
        team = Group.objects.create(name="Team")
        self.user.groups.add(team)
        create_list(group=team, name="Clients")

        self.page.goto(f"{self.live_server_url}/people")
        aside = self.page.locator(".drawer-side aside")
        # The badge count is part of the button text, so match on the name only.
        row = aside.locator("button", has_text="Clients")
        expect(row).to_be_visible()
        expect(row.locator('[data-lucide="users-round"]:visible')).to_have_count(1)
        expect(
            aside.locator('button[title="Family"] [data-lucide="users-round"]:visible')
        ).to_have_count(0)

    def test_a_group_row_appears_with_its_first_contact_and_leaves_with_the_last(self):
        # The dialogs offer every group, the sidebar only the ones holding a
        # contact: the row must follow the first creation and the last delete
        # without a reload.
        team = Group.objects.create(name="Newcomers")
        self.user.groups.add(team)

        self.page.goto(f"{self.live_server_url}/people")
        aside = self.page.locator(".drawer-side aside")
        row = aside.locator(f'button[data-scope="group:{team.id}"]')
        expect(row).to_have_count(0)

        self.page.get_by_role("button", name="New contact").click()
        dialog = self.page.locator("#people-person-dialog")
        dialog.locator('input[name="display_name"]').fill("First Member")
        dialog.locator('select[name="scope"]').select_option(f"group:{team.id}")
        dialog.get_by_role("button", name="Create").click()
        expect(row).to_be_visible()

        panel = self.page.locator("#person-panel")
        panel.get_by_title("Actions").click()
        # By role: the hidden list menu holds a "Delete" row of its own.
        self.page.locator("#people-context-menu").get_by_role(
            "button", name="Delete"
        ).click()
        self.page.locator("#app-dialog-confirm-ok").click()
        expect(row).to_have_count(0)
