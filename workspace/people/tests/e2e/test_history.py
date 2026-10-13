"""E2E: the contact panel is browser history.

Opening a contact from the list adds an entry, switching to another one in
the open panel rewrites it, and closing steps back onto the list instead of
stacking an entry a later back would reopen the contact from.

Skipped unless E2E=1.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.people.services.persons import create_person


class PeopleHistoryTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="alice")
        self.bob = create_person(owner=self.user, display_name="Bob Martin")
        self.carol = create_person(owner=self.user, display_name="Carol White")
        self.login_as(self.user)
        self.list_url = f"{self.live_server_url}/people"

    def _open(self, url):
        self.page.goto(url)
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

    def _row(self, person):
        return self.page.locator(f"#person-list a[data-person-uuid='{person.uuid}']")

    def _name(self):
        return self.page.locator("#person-panel").get_by_placeholder("Name")

    def _history_length(self):
        return self.page.evaluate("history.length")

    def _mobile(self):
        self.page.set_viewport_size({"width": 390, "height": 844})

    def test_closing_on_mobile_steps_back_and_forward_reopens(self):
        self._mobile()
        self._open(self.list_url)
        before = self._history_length()

        self._row(self.bob).click()
        expect(self._name()).to_have_value("Bob Martin")
        self.page.locator("#person-panel").get_by_role("button", name="Back").click()

        expect(self.page).to_have_url(self.list_url)
        expect(self._row(self.bob)).to_be_visible()
        # The opening entry is still ahead: closing undid it, nothing stacked.
        self.assertEqual(self._history_length(), before + 1)
        self.page.go_forward()
        expect(self.page).to_have_url(f"{self.list_url}?person={self.bob.uuid}")
        expect(self._name()).to_have_value("Bob Martin")

    def test_back_after_switching_contacts_lands_on_the_list(self):
        self._open(self.list_url)

        self._row(self.bob).click()
        expect(self._name()).to_have_value("Bob Martin")
        self._row(self.carol).click()
        expect(self._name()).to_have_value("Carol White")
        self.page.go_back()

        expect(self.page).to_have_url(self.list_url)
        expect(self.page.locator("#person-panel")).to_be_hidden()

    def test_closing_a_deep_link_rewrites_the_address(self):
        self._mobile()
        self._open(f"{self.list_url}?person={self.bob.uuid}")
        expect(self._name()).to_have_value("Bob Martin")
        before = self._history_length()

        self.page.locator("#person-panel").get_by_role("button", name="Back").click()

        expect(self.page).to_have_url(self.list_url)
        expect(self._row(self.bob)).to_be_visible()
        self.assertEqual(self._history_length(), before)

    def test_deleting_the_open_contact_leaves_no_entry_to_reopen_it(self):
        self._open(self.list_url)
        before = self._history_length()

        self._row(self.bob).click()
        expect(self._name()).to_have_value("Bob Martin")
        self.page.locator("#person-panel").get_by_title("Actions").click()
        self.page.locator("#people-context-menu").get_by_role(
            "button", name="Delete"
        ).click()
        self.page.locator("#app-dialog-confirm-ok").click()

        expect(self._row(self.bob)).to_have_count(0)
        expect(self.page).to_have_url(self.list_url)
        self.assertEqual(self._history_length(), before + 1)

    def test_back_across_a_reload_refreshes_in_place(self):
        self._open(self.list_url)
        self._row(self.bob).click()
        expect(self._name()).to_have_value("Bob Martin")
        self.page.reload()
        expect(self._name()).to_have_value("Bob Martin")
        self.page.evaluate("window.__sameLoad = true")

        self.page.go_back()

        expect(self.page).to_have_url(self.list_url)
        expect(self.page.locator("#person-panel")).to_be_hidden()
        self.assertTrue(self.page.evaluate("window.__sameLoad === true"))
