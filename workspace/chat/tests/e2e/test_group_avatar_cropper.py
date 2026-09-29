"""E2E: changing a group avatar from the info panel opens a visible cropper.

The info panel is rendered twice (side panel and mobile overlay) and CSS hides
one copy. A cropper living in the panel opened inside the hidden copy: a modal
with no box, invisible, that still made the rest of the page inert.
"""

from __future__ import annotations

import io

from PIL import Image
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember
from workspace.common.tests.e2e.base import PlaywrightTestCase


def _png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (40, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


class GroupAvatarCropperTests(PlaywrightTestCase):
    def test_the_cropper_opens_visible_from_the_desktop_info_panel(self):
        user = self.create_user(username="alice")
        conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Team", created_by=user
        )
        ConversationMember.objects.create(conversation=conv, user=user)
        self.login_as(user)
        self.page.goto(f"{self.live_server_url}/chat/{conv.uuid}")
        self.page.evaluate("document.getElementById('djDebugRoot')?.remove()")

        self.page.get_by_title("Conversation info (Alt+I)").click()
        camera = self.page.locator('[title="Change group avatar"]:visible')
        with self.page.expect_file_chooser() as chooser:
            camera.click()
        chooser.value.set_files(
            {"name": "team.png", "mimeType": "image/png", "buffer": _png_bytes()}
        )

        dialog = self.page.locator("dialog[open]").filter(
            has=self.page.get_by_role("heading", name="Crop your photo")
        )
        expect(dialog.get_by_role("button", name="Confirm")).to_be_visible()
