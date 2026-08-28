"""E2E: <conversation-avatar> in a real browser.

The element is what the sidebar row, the conversation header and the info
panel all render, and none of that is observable from Django's test client:
the header only exists after Alpine has bound it, and the geometry the two
tests below assert only exists once the browser has laid the page out.

Pins the two properties the merge is for:

1. The same conversation is labelled identically wherever it is drawn. The
   sidebar used to render server-computed initials while the header
   recomputed its own from the member list, and the two had drifted.

2. A row of avatars stays aligned whether or not each conversation has an
   uploaded picture — the geometry invariant <user-avatar> introduced, now
   covering group pictures too.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image
from playwright.sync_api import expect

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.services.avatar import process_and_save_group_avatar
from workspace.common.tests.e2e.base import PlaywrightTestCase


def _png():
    buf = BytesIO()
    Image.new("RGB", (64, 64), color="red").save(buf, format="PNG")
    buf.seek(0)
    buf.name = "group.png"
    return buf


class ConversationAvatarTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="avatar-viewer", password="pass12345")
        self.sam = self.create_user(username="sam", first_name="Sam", last_name="R")
        self.jordan = self.create_user(username="jordan", first_name="Jordan")

        # One of each branch: a DM, a group with an uploaded picture, and a
        # group falling back to the initials of its name.
        self.dm = self._conversation(Conversation.Kind.DM, [self.user, self.sam])
        self.pictured = self._conversation(
            Conversation.Kind.GROUP, [self.user, self.sam, self.jordan], title="Design"
        )
        self.lettered = self._conversation(
            Conversation.Kind.GROUP, [self.user, self.sam, self.jordan], title="Launch"
        )
        process_and_save_group_avatar(self.pictured, _png(), 0, 0, 64, 64)

    def _conversation(self, kind, members, title=""):
        conversation = Conversation.objects.create(
            kind=kind, title=title, created_by=self.user
        )
        ConversationMember.objects.bulk_create(
            ConversationMember(conversation=conversation, user=user) for user in members
        )
        return conversation

    def _open_chat(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")
        expect(self.page.locator("#conversation-list")).to_be_visible()
        self.page.wait_for_selector("conversation-avatar > *", state="attached")

    def test_the_header_labels_a_conversation_like_its_sidebar_row(self):
        self._open_chat()

        row = self.page.locator(f"#conv-item-{self.lettered.uuid} conversation-avatar")
        expect(row).to_have_text("L")

        self.page.locator(f"#conv-item-{self.lettered.uuid} button").click()
        header = self.page.locator(
            "xpath=//h3[contains(@class,'font-semibold')]"
            "/ancestor::div[contains(@class,'border-b')][1]"
            "//conversation-avatar"
        ).first
        expect(header).to_have_text("L")

    def test_a_pictured_row_and_a_lettered_row_occupy_the_same_box(self):
        self._open_chat()

        boxes = self.page.evaluate(
            """() => [...document.querySelectorAll('#conversation-list conversation-avatar')]
                       .map(el => {
                         const r = el.getBoundingClientRect();
                         return { x: r.x, w: r.width, h: r.height };
                       })"""
        )

        self.assertEqual(len(boxes), 3, "one avatar per conversation")
        first = boxes[0]
        for box in boxes[1:]:
            self.assertEqual(box["w"], first["w"], "avatars differ in width")
            self.assertEqual(box["h"], first["h"], "avatars differ in height")
            self.assertEqual(box["x"], first["x"], "avatars are not left-aligned")
        self.assertEqual(first["w"], first["h"], "the avatar box is not square")

    def test_a_broken_picture_uncovers_the_initials_without_resizing(self):
        self._open_chat()
        selector = f"#conv-item-{self.pictured.uuid} conversation-avatar"

        before = self.page.evaluate(
            f"() => document.querySelector('{selector}').getBoundingClientRect().width"
        )
        self.page.evaluate(
            f"""() => {{
                  const img = document.querySelector('{selector} img');
                  img.dispatchEvent(new Event('error'));
                }}"""
        )

        expect(self.page.locator(selector)).to_have_text("D")
        after = self.page.evaluate(
            f"() => document.querySelector('{selector}').getBoundingClientRect().width"
        )
        self.assertEqual(after, before, "losing the picture resized the avatar")
