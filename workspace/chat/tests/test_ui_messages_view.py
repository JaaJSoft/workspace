"""The server-rendered message list: its attachments, and a golden snapshot
of the whole partial."""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
    PinnedMessage,
    Reaction,
)

User = get_user_model()


class ConversationMessagesViewAttachmentTests(TestCase):
    """The messages partial must render every attachment of a message."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.DM, created_by=self.user
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.user
        )
        self.message = Message.objects.create(
            conversation=self.conversation, author=self.user, body="see files"
        )
        self.url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.conversation.uuid},
        )
        self.client.force_login(self.user)

    def _attach(self, name, mime, category):
        return MessageAttachment.objects.create(
            message=self.message,
            file=SimpleUploadedFile(name, b"x", content_type=mime),
            original_name=name,
            mime_type=mime,
            category=category,
            size=1,
        )

    def test_all_attachments_render(self):
        # Attachments reach the page as the JSON payload the
        # <chat-message-group> shell turns into the media mosaic and file
        # chips (including the data-attachment-* attributes the viewer's
        # prev/next navigation walks) - so assert every attachment is in the
        # payload, sorted into the right bucket.
        self._attach("a.png", "image/png", "image")
        self._attach("b.png", "image/png", "image")
        self._attach("c.mp4", "video/mp4", "video")
        self._attach("d.pdf", "application/pdf", "document")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        for att in self.message.media_attachments:
            self.assertIn(
                f'{{"uuid": "{att.uuid}", "name": "{att.original_name}"', html
            )
        for att in self.message.file_attachments:
            self.assertIn(
                f'{{"uuid": "{att.uuid}", "name": "{att.original_name}"', html
            )
        self.assertEqual(len(self.message.media_attachments), 3)
        self.assertEqual(len(self.message.file_attachments), 1)

    def test_single_image_renders(self):
        att = self._attach("solo.png", "image/png", "image")
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn(f'"uuid": "{att.uuid}"', html)
        self.assertIn('"is_image": true', html)


# .golden.txt, not .html: djlint lints every .html under workspace/, and a
# captured response is not a template it can format.
GOLDEN_MEMBER_LIST = (
    Path(__file__).resolve().parent / "data" / "member_message_list.golden.txt"
)
GOLDEN_DAY = datetime(2026, 3, 4, 9, 0, tzinfo=UTC)

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def collapse(html):
    """One line, single spaces - both panes render the same template file, so
    only real markup differences survive this."""
    return re.sub(r"\s+", " ", html).strip()


def anonymise(html, user_ids):
    """Replace the two things that differ between runs: uuids and user ids.

    UUIDs are numbered in order of first appearance, so the mapping is itself
    part of what the snapshot pins - groups rendered in a different order
    renumber and fail.
    """
    numbered = {}

    def _number(match):
        return numbered.setdefault(match.group(0), f"<uuid-{len(numbered) + 1}>")

    html = _UUID_RE.sub(_number, html)
    for index, user_id in enumerate(user_ids, start=1):
        html = html.replace(f'author-id="{user_id}"', f'author-id="<user-{index}>"')
    return html


class MemberMessageListSnapshotTests(TestCase):
    """The message pane's markup, held against a capture of itself.

    Regenerate the golden only for a deliberate, reviewed change: write
    ``render_member_list()``'s output to GOLDEN_MEMBER_LIST and read the diff.

    Compared with whitespace collapsed: wrapping a block in {% if %} re-indents
    it, which moves the indentation inside the response and nothing else.
    Every tag, attribute, class and handler still has to match exactly.
    """

    maxDiff = None

    def setUp(self):
        cache.clear()
        self.host = User.objects.create_user(
            username="snaphost", password="pw", first_name="Hana", last_name="Host"
        )
        self.other = User.objects.create_user(username="snapother", password="pw")
        self.conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.host, title="Snapshot"
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.host
        )
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.other
        )

        host_msg = self._message(
            minutes=0, author=self.host, body="hello from the host"
        )
        other_msg = self._message(
            minutes=1, author=self.other, body="hello from a member"
        )
        self._message(
            minutes=2,
            author=self.host,
            body="Call started",
            kind=Message.Kind.SYSTEM,
            tool_data={"type": "call", "state": "active"},
        )
        self._message(
            minutes=3,
            author=self.host,
            body="answering the other member",
            reply_to=other_msg,
        )
        Reaction.objects.create(message=other_msg, user=self.host, emoji="\U0001f44d")
        PinnedMessage.objects.create(
            conversation=self.conversation, message=host_msg, pinned_by=self.host
        )
        self.client.force_login(self.host)

    def tearDown(self):
        cache.clear()

    def _message(self, *, minutes, body, author, **kwargs):
        """A message at a fixed instant - created_at is auto_now_add, so the
        wall clock has to be overwritten after the insert."""
        msg = Message.objects.create(
            conversation=self.conversation,
            author=author,
            body=body,
            **kwargs,
        )
        Message.objects.filter(pk=msg.pk).update(
            created_at=GOLDEN_DAY + timedelta(minutes=minutes)
        )
        msg.refresh_from_db()
        return msg

    def render_member_list(self):
        url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.conversation.uuid},
        )
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return anonymise(resp.content.decode(), [self.host.id, self.other.id])

    def test_the_member_list_matches_the_golden_render(self):
        self.assertEqual(
            collapse(self.render_member_list()),
            collapse(GOLDEN_MEMBER_LIST.read_text(encoding="utf-8")),
        )
