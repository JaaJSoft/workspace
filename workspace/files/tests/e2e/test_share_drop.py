"""E2E: a signed-out visitor drops a file into a folder and sees nothing else.

Only a real browser exercises this. The upload answers 204 with no body, so
there is nothing for the test client to assert on beyond a status code, and
the guarantee that matters is what the page does NOT render.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileShareLink


class ShareDropLinkTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="drop-owner")
        self.folder = File.objects.create(
            owner=self.owner, name="Drop", node_type=File.NodeType.FOLDER
        )
        # Something already inside, which the visitor must never see.
        File.objects.create(
            owner=self.owner,
            name="private.txt",
            node_type=File.NodeType.FILE,
            parent=self.folder,
        )
        self.link = FileShareLink.objects.create(
            file=self.folder,
            created_by=self.owner,
            mode=FileShareLink.Mode.DROP,
        )

    def test_a_signed_out_visitor_uploads_and_sees_no_listing(self):
        self.page.goto(f"{self.live_server_url}/files/shared/{self.link.token}")

        expect(self.page.get_by_test_id("drop-zone")).to_be_visible()
        self.assertNotIn("private.txt", self.page.content())

        self.page.set_input_files(
            "input[type=file]",
            files=[
                {
                    "name": "from-outside.txt",
                    "mimeType": "text/plain",
                    "buffer": b"hello",
                }
            ],
        )
        expect(self.page.get_by_test_id("drop-done")).to_be_visible()

        self.assertTrue(
            File.objects.filter(parent=self.folder, name="from-outside.txt").exists()
        )

    def test_the_listing_endpoint_is_closed_to_a_drop_link(self):
        response = self.page.request.get(
            f"{self.live_server_url}/api/v1/files/shared/{self.link.token}/entries"
        )
        self.assertEqual(response.status, 404)
