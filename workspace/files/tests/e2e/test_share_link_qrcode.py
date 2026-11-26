"""E2E: a public link's QR code unfolds under its row, loads and downloads.

The JS tests hold the component's state and the API tests what the image
encodes. What only a browser shows is that the row's button is wired to that
state, that the address the row builds is one the server answers with an
image, and that the PNG arrives as a file named after the node.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File, FileShareLink


class ShareLinkQRCodeTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="qr-owner")
        self.doc = File.objects.create(
            owner=self.user,
            name="report.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        self.link = FileShareLink.objects.create(file=self.doc, created_by=self.user)
        self.login_as(self.user)

    def _open_share_modal(self):
        self.page.goto(f"{self.live_server_url}/files")
        expect(
            self.page.locator(f'[data-uuid="{self.doc.uuid}"]').first
        ).to_be_visible()
        self.page.evaluate(
            """([uuid, name]) => window.dispatchEvent(new CustomEvent(
                'open-share-modal', { detail: { uuid, name, nodeType: 'file' } }
            ))""",
            [str(self.doc.uuid), self.doc.name],
        )
        return self.page.locator("dialog[open]")

    def test_the_button_unfolds_a_code_that_loads_and_folds_it_back(self):
        dialog = self._open_share_modal()
        button = dialog.get_by_role("button", name="QR code")
        image = dialog.get_by_role("img", name="QR code of the link")
        expect(image).to_have_count(0)

        button.click()

        expect(image).to_be_visible()
        # decode() rejects on an image the server refused or could not draw.
        width = image.evaluate("img => img.decode().then(() => img.naturalWidth)")
        self.assertGreater(width, 0)
        expect(dialog.get_by_text("Could not load the QR code")).to_have_count(0)

        button.click()
        expect(image).to_have_count(0)

    def test_the_png_downloads_under_the_name_of_the_node(self):
        dialog = self._open_share_modal()
        dialog.get_by_role("button", name="QR code").click()

        with self.page.expect_download() as download:
            dialog.get_by_role("link", name="Download PNG").click()

        self.assertEqual(download.value.suggested_filename, "report.txt-qr.png")
        with open(download.value.path(), "rb") as saved:
            self.assertEqual(saved.read(8), b"\x89PNG\r\n\x1a\n")

    def test_a_code_the_server_refuses_says_so_instead_of_a_broken_image(self):
        dialog = self._open_share_modal()
        button = dialog.get_by_role("button", name="QR code")
        expect(button).to_be_visible()
        # Revoked from another tab while this dialog still lists it.
        self.link.delete()

        button.click()

        expect(dialog.get_by_text("Could not load the QR code")).to_be_visible()
        expect(dialog.get_by_role("img", name="QR code of the link")).to_have_count(0)
