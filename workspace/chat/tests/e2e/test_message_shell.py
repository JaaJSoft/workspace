"""E2E cover for the <chat-message-group> shell element.

The shell builds the message bubble chrome client-side (message_shell.js),
for both the server-rendered path (message_group.html writes the element
with slotted content) and the optimistic path (messages.js creates it with
the `pending` attribute). Custom-element behaviour is out of reach for the
node:vm unit-test loader — no DOM — so the rendered shell is pinned here:
own/other alignment and colour variants, the reply quote, the attachment
fragments, the pending variant, and compact-mode participation.

Skipped unless E2E=1 is set.
"""

from __future__ import annotations

from django.core.files.uploadedfile import SimpleUploadedFile
from playwright.sync_api import expect

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
)
from workspace.chat.services.rendering import render_message_body
from workspace.common.tests.e2e.base import PlaywrightTestCase


class MessageShellRenderingTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="shell-tester", password="pass12345")
        self.peer = self.create_user(username="shell-peer")

        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.user,
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        ConversationMember.objects.create(conversation=self.conv, user=self.peer)

        self.other_msg = self._message(self.peer, "a message from the peer")
        self.own_msg = self._message(self.user, "an own message")
        self.reply_msg = self._message(
            self.user, "an answer to the peer", reply_to=self.other_msg
        )

    def _message(self, author, body, **kwargs):
        return Message.objects.create(
            conversation=self.conv,
            author=author,
            body=body,
            body_html=render_message_body(body),
            **kwargs,
        )

    def _open_conversation(self):
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")
        list_root = self.page.locator("#conversation-list")
        expect(list_root.get_by_text("shell-peer")).to_be_visible()
        list_root.get_by_text("shell-peer").click()
        expect(self.page.locator(f"#msg-{self.own_msg.uuid}")).to_be_visible()

    def _classes(self, locator):
        return set((locator.get_attribute("class") or "").split())

    def test_own_and_other_groups_render_alignment_and_colour_variants(self):
        self._open_conversation()

        own_bubble = self.page.locator(f"#msg-{self.own_msg.uuid}")
        own_group = self.page.locator("chat-message-group[own]", has=own_bubble).first
        self.assertIn("msg-group-end", self._classes(own_group))
        self.assertIn("flex-row-reverse", self._classes(own_group))
        self.assertIn("bg-info/15", self._classes(own_bubble))
        expect(own_bubble).to_have_attribute("data-body", "an own message")
        # The sender's avatar rides along in the avatar column.
        expect(own_group.locator("user-avatar").first).to_be_visible()
        # A settled group shows the timestamp footer, not the pending spinner.
        expect(own_group.locator(".loading-dots")).to_have_count(0)

        other_bubble = self.page.locator(f"#msg-{self.other_msg.uuid}")
        other_group = self.page.locator(
            "chat-message-group:not([own])", has=other_bubble
        ).first
        self.assertIn("msg-group-start", self._classes(other_group))
        self.assertNotIn("flex-row-reverse", self._classes(other_group))
        self.assertIn("bg-base-200", self._classes(other_bubble))
        # Only the other side gets the author header.
        expect(other_group.get_by_text("shell-peer", exact=True)).to_be_visible()

    def test_reply_quote_links_to_the_quoted_message(self):
        self._open_conversation()

        quote = self.page.locator(
            f'#msg-{self.reply_msg.uuid} a[href="#msg-{self.other_msg.uuid}"]'
        )
        expect(quote).to_be_visible()
        expect(quote).to_contain_text("shell-peer")
        expect(quote).to_contain_text("a message from the peer")

    def test_attachments_render_media_preview_and_file_chip(self):
        media_msg = self._message(self.user, "with attachments")
        image = MessageAttachment.objects.create(
            message=media_msg,
            file=SimpleUploadedFile("photo.png", b"fakepng", content_type="image/png"),
            original_name="photo.png",
            mime_type="image/png",
            type="png",
            category="image",
            size=7,
        )
        pdf = MessageAttachment.objects.create(
            message=media_msg,
            file=SimpleUploadedFile(
                "doc.pdf", b"%PDF-fake", content_type="application/pdf"
            ),
            original_name="doc.pdf",
            mime_type="application/pdf",
            type="pdf",
            category="document",
            size=123456,
        )
        self._open_conversation()

        bubble = self.page.locator(f"#msg-{media_msg.uuid}")
        # Single image: full preview from the API URL, stamped for the
        # viewer's prev/next navigation.
        img = bubble.locator(f'img[src="/api/v1/chat/attachments/{image.uuid}"]')
        expect(img).to_be_attached()
        expect(
            bubble.locator(f'[data-attachment-uuid="{image.uuid}"]')
        ).to_be_attached()
        # Generic file: chip with name + human-readable size and the
        # save-to-files affordance.
        chip = bubble.locator(f'[data-attachment-uuid="{pdf.uuid}"]')
        expect(chip).to_be_visible()
        expect(chip).to_contain_text("doc.pdf")
        # formatFileSize(123456) mirrors Django's filesizeformat, non-breaking
        # space included - the expected string carries a literal U+00A0.
        expect(chip).to_contain_text("120.6 KB")
        expect(chip.locator('button[title="Save to Files"]')).to_be_attached()

    def test_pending_variant_renders_spinner_and_reduced_opacity(self):
        self._open_conversation()

        # Drive the real production path deterministically (no network
        # round-trip to race): the mixin method that sendMessage() calls.
        self.page.evaluate(
            """() => {
              const scope = Alpine.$data(document.getElementById('messages-container'));
              scope._injectOptimisticMessage(
                '_e2e_pending', 'optimistic body', {author: 'shell-peer', body: 'quoted text'}, null,
              );
            }"""
        )

        pending = self.page.locator("chat-message-group#_e2e_pending[pending][own]")
        expect(pending).to_be_visible()
        expect(pending).to_contain_text("optimistic body")
        # The pending extras: reduced opacity on the bubble, a spinner in
        # place of the timestamp, and the (non-interactive) reply quote.
        self.assertIn("opacity-70", self._classes(pending.locator(".msg-bubble")))
        expect(pending.locator(".loading-dots")).to_be_visible()
        expect(pending.get_by_text("quoted text")).to_be_visible()
        expect(pending.locator("a")).to_have_count(0)

        self.page.evaluate(
            """() => {
              Alpine.$data(document.getElementById('messages-container'))
                ._removeOptimisticMessage('_e2e_pending');
            }"""
        )
        expect(pending).to_have_count(0)

    def test_sent_message_replaces_the_pending_bubble_with_the_server_shell(self):
        self._open_conversation()

        composer = self.page.locator('textarea[placeholder="Type a message..."]')
        composer.fill("round trip through the shell")
        composer.press("Enter")

        # After the POST + refresh, the server-rendered shell owns the text:
        # a real bubble (data-body only exists on settled messages) with no
        # pending group left behind.
        settled = self.page.locator(
            '#message-list-items .msg-bubble[data-body="round trip through the shell"]'
        )
        expect(settled).to_be_visible()
        expect(self.page.locator("chat-message-group[pending]")).to_have_count(0)

    def test_compact_mode_restyles_the_shell_reactively(self):
        self._open_conversation()

        bubble = self.page.locator(f"#msg-{self.own_msg.uuid}")
        self.assertIn("px-3", self._classes(bubble))

        self.page.evaluate("() => window.updateChatPref('compactMessageView', true)")

        # :class bindings written by the shell flip with the preference —
        # no refetch, no rebuild.
        self.page.wait_for_function(
            f"""() => document.getElementById('msg-{self.own_msg.uuid}')
                  .classList.contains('px-2.5')"""
        )
        self.assertNotIn("px-3", self._classes(bubble))
