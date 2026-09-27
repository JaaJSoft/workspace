"""E2E cover for importing from the Photos page. Skipped unless E2E=1 is set."""

from __future__ import annotations

from django.core.cache import cache
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File
from workspace.photos.tests.images import jpeg_bytes

TILES = "#timeline-grid [data-uuid]"


class PhotosImportTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="importer", is_staff=True)
        self.login_as(self.user)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def test_imported_photos_land_in_pictures_and_on_the_timeline(self):
        self.page.goto(f"{self.live_server_url}/photos")
        expect(self.page.get_by_text("No photos yet")).to_be_visible()

        self.page.set_input_files(
            "#photos-import-input",
            [
                {
                    "name": "IMG_0001.jpg",
                    "mimeType": "image/jpeg",
                    "buffer": jpeg_bytes(),
                },
                {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hello"},
            ],
        )

        expect(self.page.locator(TILES)).to_have_count(1)
        folder = File.objects.get(owner=self.user, name="Pictures", parent__isnull=True)
        self.assertEqual(
            list(File.objects.filter(parent=folder).values_list("name", flat=True)),
            ["IMG_0001.jpg"],
        )

    def test_dragging_files_over_the_listing_shows_the_drop_zone(self):
        self.page.goto(f"{self.live_server_url}/photos")
        zone = self.page.get_by_test_id("photos-drop-zone")

        self.page.evaluate(
            """() => {
              const dt = new DataTransfer();
              dt.items.add(new File(['x'], 'a.jpg', { type: 'image/jpeg' }));
              document.querySelector('[data-testid=photos-drop-zone]').parentElement
                .dispatchEvent(new DragEvent('dragenter', { dataTransfer: dt, bubbles: true }));
            }"""
        )

        expect(zone).to_be_visible()
        expect(zone).to_contain_text("They are saved to Pictures in Files")
