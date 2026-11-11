"""E2E: files shared from another app go up to the folder the user picks.

Android and Windows hand a share to the installed app as a multipart POST to
the manifest's share_target action. The service worker keeps the files in
IndexedDB and sends the app to /files?share_target=<id>, where the folder
picker asks where they go. A unit test stubs both halves; whether a File
survives the trip through IndexedDB and reaches the upload queue is the
browser's business.
"""

from __future__ import annotations

from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File

# What the operating system sends: the page cannot build that request, but a
# fetch from a controlled page goes through the same service worker handler.
SHARE_SCRIPT = """
async () => {
  const form = new FormData();
  form.append('files', new File(['shared bytes'], 'shared.txt', { type: 'text/plain' }));
  const response = await fetch('/files/share-target', { method: 'POST', body: form });
  return response.url;
}
"""


class ShareTargetTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="share-target")
        self.inbox = File.objects.create(
            owner=self.user, name="Inbox", node_type=File.NodeType.FOLDER
        )
        self.login_as(self.user)

    def _controlled_files_page(self):
        self.page.goto(f"{self.live_server_url}/files")
        self.page.evaluate("navigator.serviceWorker.ready")
        # A page loaded before its worker activated stays uncontrolled.
        self.page.reload()
        self.page.wait_for_function("!!navigator.serviceWorker.controller")

    def test_a_shared_file_is_uploaded_to_the_picked_folder(self):
        self._controlled_files_page()

        landing = self.page.evaluate(SHARE_SCRIPT)
        self.assertRegex(landing, r"/files\?share_target=[0-9a-f-]{36}$")
        self.page.goto(landing)

        dialog = self.page.locator("dialog[open]").last
        expect(dialog.get_by_text("Save shared files")).to_be_visible()
        dialog.get_by_role("button", name="Inbox").click()
        dialog.get_by_role("button", name="Save here").click()

        self.page.wait_for_url(f"**/files/{self.inbox.uuid}")
        expect(
            self.page.locator("#folder-browser").get_by_text("shared.txt")
        ).to_be_visible()
        shared = File.objects.get(owner=self.user, name="shared.txt")
        self.assertEqual(shared.parent_id, self.inbox.uuid)
        with shared.content.open("rb") as f:
            self.assertEqual(f.read(), b"shared bytes")

        # The share is consumed: the address no longer names it, and the
        # inbox no longer holds it.
        share_id = landing.rsplit("=", 1)[1]
        self.assertIsNone(
            self.page.evaluate("(id) => shareTargetInbox.get(id)", share_id)
        )

    def test_a_share_the_browser_no_longer_holds_is_reported(self):
        self.page.goto(f"{self.live_server_url}/files?share_target=gone")

        expect(
            self.page.get_by_text("The shared files could not be received")
        ).to_be_visible()
        expect(self.page.locator("dialog[open]")).to_have_count(0)
        self.assertTrue(self.page.url.endswith("/files"))
