"""E2E: the ``?open=<uuid>`` deep link opens the viewer on a shared file.

A search hit on a file shared with the user lands on
``/files?shared=1&open=<uuid>``. The recipient has no edit right, so the
file metadata endpoint answers 404 to them; a viewer that only asks the API
for the file's name and type never opens, and the user is left on the
listing with nothing to show for the click.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import FileShare
from workspace.files.services import FileService


class ViewerDeepLinkTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="owner")
        self.recipient = self.create_user(username="recipient")
        folder = FileService.create_folder(self.owner, "Private")
        self.file = FileService.create_file(
            owner=self.owner,
            name="Minutes.txt",
            parent=folder,
            content=SimpleUploadedFile(
                "Minutes.txt", b"the treasurer resigned\n", content_type="text/plain"
            ),
            acting_user=self.owner,
        )
        FileShare.objects.create(
            file=self.file, shared_by=self.owner, shared_with=self.recipient
        )

    def test_a_read_only_share_opens_in_the_viewer_from_shared_with_me(self):
        self.login_as(self.recipient)
        self.page.goto(f"{self.live_server_url}/files?shared=1&open={self.file.uuid}")

        dialog = self.page.locator("dialog[open]").filter(
            has=self.page.locator("h3", has_text="Minutes.txt")
        )
        expect(dialog).to_be_visible()
