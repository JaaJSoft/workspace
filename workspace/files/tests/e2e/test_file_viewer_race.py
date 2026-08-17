"""E2E cover for the file viewer modal's stale-response race.

Open file A, then file B while A's viewer request is still in flight: A's
late response must neither paint its viewer into the modal nor execute its
scripts against B's DOM. Instead of racing real timing, A's request is held
at the network layer and only released after B has fully rendered - the
worst-case ordering the old hand-rolled loader lost (it appended A's markup
below B's and ran A's scripts).

The text viewer doubles as the script probe: its response carries a script
that defines ``window.textViewerMonaco`` at top level, so the global's very
existence proves A's scripts ran. B is an image - its viewer renders without
any third-party network.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

import base64
import time

from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.services import FileService

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FileViewerModalRaceTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="viewer-race")
        self.text_file = FileService.create_file(
            owner=self.user,
            name="alpha.txt",
            content=SimpleUploadedFile(
                "alpha.txt", b"alpha file body\n" * 40, content_type="text/plain"
            ),
            acting_user=self.user,
        )
        self.image_file = FileService.create_file(
            owner=self.user,
            name="bravo.png",
            content=SimpleUploadedFile("bravo.png", PNG_1PX, content_type="image/png"),
            acting_user=self.user,
        )

    def _open_viewer(self, file_obj):
        self.page.evaluate(
            """(f) => window.dispatchEvent(
                new CustomEvent('open-file-viewer', { detail: f })
            )""",
            {
                "uuid": str(file_obj.uuid),
                "name": file_obj.name,
                "type": file_obj.type,
            },
        )

    def test_a_slow_previous_file_never_paints_into_the_new_ones_viewer(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/files")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

        held = {}
        self.page.route(
            f"**/files/view/{self.text_file.uuid}",
            lambda route: held.setdefault("text", route),
        )

        # Open A (text). Alpine may still be booting the modal's listeners,
        # so retry the dispatch until A's viewer request is actually held.
        deadline = time.monotonic() + 5
        while "text" not in held and time.monotonic() < deadline:
            self._open_viewer(self.text_file)
            self.page.wait_for_timeout(100)
        self.assertIn("text", held, "file A's viewer request must be in flight")

        # Open B (image) while A is still loading, and let it render fully.
        self._open_viewer(self.image_file)
        modal = self.page.locator('[x-data="fileViewerModal()"] .modal-box')
        expect(modal.locator(f'img[src*="{self.image_file.uuid}"]')).to_be_visible(
            timeout=10000
        )

        # Worst-case ordering: A's response lands only now.
        with self.page.expect_response(f"**/files/view/{self.text_file.uuid}"):
            held["text"].continue_()
        # Give a (buggy) merge every chance to paint before asserting.
        self.page.wait_for_timeout(300)

        # A's scripts must not have executed...
        self.assertEqual(
            self.page.evaluate("typeof window.textViewerMonaco"), "undefined"
        )
        # ...and A's viewer markup must not be in the modal - only B's.
        expect(modal.locator('[x-data^="textViewerMonaco"]')).to_have_count(0)
        expect(modal.locator(f'img[src*="{self.image_file.uuid}"]')).to_be_visible()
