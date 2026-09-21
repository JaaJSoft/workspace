"""Import a .vcf through the dialog and see the contacts and the list appear."""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.people.models import Person, PersonList

FIXTURE = Path(__file__).parent.parent / "vcards" / "ios.vcf"


class VCardImportFlowTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.login_as(self.user)
        self.page.set_viewport_size({"width": 1280, "height": 900})

    def test_import_dialog_reports_and_refreshes(self):
        self.page.goto(f"{self.live_server_url}/people")
        self.page.get_by_role("button", name="Import contacts").click()
        dialog = self.page.locator("#people-import-dialog")
        expect(dialog).to_be_visible()
        expect(dialog.get_by_role("button", name="Import")).to_be_disabled()
        dialog.locator('input[name="file"]').set_input_files(str(FIXTURE))
        expect(dialog.get_by_role("button", name="Import")).to_be_enabled()
        dialog.get_by_role("button", name="Import").click()

        expect(dialog.locator(".stats")).to_be_visible()
        expect(dialog.locator(".stat").nth(0)).to_contain_text("1")
        expect(dialog.locator(".stat").nth(2)).to_contain_text("1")
        expect(self.page.locator("#person-list")).to_contain_text("Jane Marie Doe")
        expect(self.page.locator("aside")).to_contain_text("Friends")
        dialog.get_by_role("button", name="Done").click()
        # A closed daisyUI modal keeps its box (opacity 0), so ask the element.
        self.assertFalse(dialog.evaluate("d => d.open"))

        jane = Person.objects.get(owner=self.user)
        self.assertEqual(jane.source, "import")
        self.assertEqual(PersonList.objects.get(owner=self.user).members.count(), 1)

    def test_bad_file_shows_the_error_in_the_dialog(self):
        self.page.goto(f"{self.live_server_url}/people?action=import")
        dialog = self.page.locator("#people-import-dialog")
        expect(dialog).to_be_visible()
        dialog.locator('input[name="file"]').set_input_files(
            {"name": "notes.vcf", "mimeType": "text/vcard", "buffer": b"hello"}
        )
        dialog.get_by_role("button", name="Import").click()
        expect(dialog.locator("inline-alert")).to_contain_text("not a vCard file")
        self.assertFalse(Person.objects.exists())

    def test_export_row_in_the_list_menu_downloads_a_vcf(self):
        from workspace.people.services.lists import add_members, create_list
        from workspace.people.services.persons import create_person

        jane = create_person(owner=self.user, display_name="Jane Doe")
        friends = create_list(owner=self.user, name="Friends")
        add_members(friends, [jane])
        self.page.goto(f"{self.live_server_url}/people")
        self.page.locator("aside").get_by_role(
            "button", name=re.compile(r"^Friends")
        ).click(button="right")
        menu = self.page.locator("#people-context-menu")
        with self.page.expect_download() as download:
            menu.get_by_role("button", name="Export vCard").click()
        self.assertEqual(download.value.suggested_filename, "Friends.vcf")

    def test_panel_button_downloads_the_contact(self):
        from workspace.people.services.persons import create_person

        jane = create_person(owner=self.user, display_name="Jane Doe")
        self.page.goto(f"{self.live_server_url}/people?person={jane.uuid}")
        panel = self.page.locator("#person-panel")
        with self.page.expect_download() as download:
            panel.get_by_role("button", name="Export vCard").click()
        self.assertEqual(download.value.suggested_filename, "Jane Doe.vcf")
