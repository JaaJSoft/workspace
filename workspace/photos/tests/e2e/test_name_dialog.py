"""E2E: the header of the dialog naming a person shows the face being named,
or the dialog's icon when the face has no cover. Skipped unless E2E=1."""

from __future__ import annotations

from django.core.cache import cache
from django.test import override_settings
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase

COVER = "/static/icons/favicon.svg"


@override_settings(PHOTOS_FACES_ENABLED=True)
class NameDialogHeaderTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user(username="namer"))
        self.page.goto(f"{self.live_server_url}/photos")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")
        self.dialog = self.page.locator("#name-person-dialog")

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _open(self, target):
        self.page.evaluate(
            """(target) => {
              const app = Alpine.$data(document.querySelector('[x-data="photosApp()"]'));
              app.nameDialog = { target, query: '', results: [], loading: false, saving: false };
              document.getElementById('name-person-dialog').showModal();
            }""",
            target,
        )
        expect(self.dialog).to_be_visible()

    def test_an_unnamed_face_shows_its_cover(self):
        self._open({"cover_url": COVER, "person": None})

        expect(self.dialog.locator("h3")).to_have_text("Who is this?")
        cover = self.dialog.locator("img").first
        expect(cover).to_be_visible()
        expect(cover).to_have_attribute("src", COVER)
        expect(self.dialog.locator('[data-lucide="user-round-pen"]')).to_be_hidden()

    def test_a_named_face_without_cover_shows_the_icon(self):
        self._open({"cover_url": None, "person": "some-uuid"})

        expect(self.dialog.locator("h3")).to_have_text("Change name")
        expect(self.dialog.locator("img").first).to_be_hidden()
        expect(self.dialog.locator('[data-lucide="user-round-pen"]')).to_be_visible()
