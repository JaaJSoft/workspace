"""E2E: a signed-out visitor drops a file into a folder and sees nothing else.

Only a real browser exercises this. The upload answers 204 with no body, so
there is nothing for the test client to assert on beyond a status code. The
test below pins down half of the "sees nothing else" guarantee: the drop
page's own markup never grows a listing. The other half - that a drop-mode
link can never resolve a listing at all, not just that this particular page
doesn't render one - is covered at the Django level by
``workspace.files.tests.test_ui_views.SharedLinkPageTests.test_a_drop_mode_folder_link_never_resolves_node``,
which replaced the old ``GET .../entries`` endpoint (removed along with the
client-rendered browser) that used to carry this half.
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


class ShareBothLinkTests(PlaywrightTestCase):
    """A read-and-upload link lists the folder above the drop zone; a file
    the visitor just sent has to show up there without a manual refresh."""

    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="both-owner")
        self.folder = File.objects.create(
            owner=self.owner, name="Inbox", node_type=File.NodeType.FOLDER
        )
        File.objects.create(
            owner=self.owner,
            name="existing.txt",
            node_type=File.NodeType.FILE,
            parent=self.folder,
        )
        self.link = FileShareLink.objects.create(
            file=self.folder,
            created_by=self.owner,
            mode=FileShareLink.Mode.BOTH,
        )

    def test_an_uploaded_file_appears_in_the_listing(self):
        self.page.goto(f"{self.live_server_url}/files/shared/{self.link.token}")
        listing = self.page.locator("#shared-content")
        expect(listing).to_contain_text("existing.txt")
        self.assertNotIn("from-outside.txt", listing.inner_text())

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

        expect(self.page.locator("#shared-content")).to_contain_text("from-outside.txt")
