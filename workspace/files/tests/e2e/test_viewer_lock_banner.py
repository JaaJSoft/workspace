"""E2E: an editor opened on a file someone else is editing says who.

The text and markdown editors open read-only on a file another user holds
the lock of, under a banner naming that user with a way to force the lock.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.files.base import ContentFile
from django.utils import timezone
from playwright.sync_api import expect

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.files.models import File
from workspace.files.services import tree


class ViewerLockBannerTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.create_user(username="owner")
        self.editor = self.create_user(username="bob")
        self.login_as(self.owner)

    def _assert_banner_names_the_editor(self, name, mime_type):
        file_obj = tree.create_file(
            self.owner,
            name,
            content=ContentFile(b"# Draft\n", name=name),
            mime_type=mime_type,
        )
        now = timezone.now()
        File.objects.filter(pk=file_obj.pk).update(
            locked_by=self.editor,
            locked_at=now,
            lock_expires_at=now + timedelta(minutes=10),
        )

        self.page.goto(f"{self.live_server_url}/files?open={file_obj.uuid}")

        panel = self.page.locator("#viewer-panel")
        expect(panel.get_by_text("bob is editing this file")).to_be_visible(
            timeout=20000
        )
        expect(panel.get_by_role("button", name="Force unlock")).to_be_visible()

    def test_text_editor(self):
        self._assert_banner_names_the_editor("draft.txt", "text/plain")

    def test_markdown_editor(self):
        self._assert_banner_names_the_editor("draft.md", "text/markdown")
