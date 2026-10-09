"""E2E: the processing spinner goes away once the file's pipeline settles.

The listing renders the spinner server-side, so nothing would take it down
before a reload: the page listens for the ``files.processing_settled`` push
and re-renders. The push is dispatched by hand here, as the SSE client does
on receipt.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File

SPINNER = "[title^='Processing:']"


class ProcessingSpinnerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="owner")
        self.file = File.objects.create(
            owner=self.owner,
            name="upload.txt",
            node_type=File.NodeType.FILE,
            processing_status=File.ProcessingStatus.PENDING,
        )
        self.login_as(self.owner)

    def _settle(self):
        File.objects.filter(pk=self.file.pk).update(
            processing_status=File.ProcessingStatus.READY
        )
        self.page.evaluate(
            "(uuid) => window.dispatchEvent(new CustomEvent("
            "'sse:files.processing_settled', { detail: { file_uuid: uuid } }))",
            str(self.file.uuid),
        )

    def test_the_listing_drops_the_spinner_without_a_reload(self):
        self.page.goto(f"{self.live_server_url}/files")
        row = self.page.locator(f"#folder-browser [data-uuid='{self.file.uuid}']")
        expect(row.locator(SPINNER)).to_have_count(1)

        self._settle()

        expect(row.locator(SPINNER)).to_have_count(0)

    def test_the_open_properties_panel_drops_its_processing_notice(self):
        self.page.goto(f"{self.live_server_url}/files")
        self.page.evaluate(
            "(uuid) => window.dispatchEvent(new CustomEvent('open-properties', "
            "{ detail: { uuid, nodeType: 'file' } }))",
            str(self.file.uuid),
        )
        content = self.page.locator("#properties-sidebar #properties-content")
        expect(content).to_contain_text("This file is being scanned")

        self._settle()

        expect(self.page.locator("#properties-sidebar")).to_be_visible()
        expect(content).not_to_contain_text("This file is being scanned")
        expect(content).to_contain_text("upload.txt")
