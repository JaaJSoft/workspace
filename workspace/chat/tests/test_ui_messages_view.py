"""The server-rendered message list, on both surfaces that load it.

The member pane and the public meeting page render the same partial, so this
module holds three kinds of assertion: that the member's HTML has not moved
(a golden snapshot), that the guest's HTML is the same HTML minus the controls
a guest has no endpoint for (a parity test that normalises exactly those
differences and nothing else), and that the guest view's scoping - the
occurrence floor, the cursor, the token gate - holds.
"""

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from workspace.chat.models import (
    Conversation,
    ConversationMember,
    MeetingGuest,
    Message,
    MessageAttachment,
    PinnedMessage,
    Reaction,
)
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import create_meeting

from .meeting_fixtures import guest_with_token, make_event

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


class ConversationMessagesGuestAuthorTests(TestCase):
    """A meeting guest has no user row, so the group header must omit
    author-id rather than render the literal string "None" - the avatar
    element would then request /api/v1/users/None/avatar."""

    def setUp(self):
        self.user = User.objects.create_user(username="hosty", password="pw")
        event = make_event(self.user)
        self.meeting = create_meeting(event, self.user)
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=timezone.now(),
            token_hash="c" * 64,
        )
        Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.user,
            body="member says hi",
        )
        Message.objects.create(
            conversation=self.meeting.conversation,
            guest=self.guest,
            body="guest says hi",
        )
        self.url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.meeting.conversation_id},
        )
        self.client.force_login(self.user)

    def test_a_guest_group_carries_no_author_id(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn('author-id="None"', html)
        self.assertIn(f'author-id="{self.user.id}"', html)


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
    """The member pane's markup, held against a capture of itself.

    The guest pane renders the same templates through the same tags, so every
    capability gate added for a guest is a chance to move what a member sees.
    The golden file is a render of this scenario taken before those gates
    existed; regenerate it only for a deliberate, reviewed change.

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
        event = make_event(self.host, start=GOLDEN_DAY, title="Snapshot")
        self.meeting = create_meeting(event, self.host)
        self.conversation = self.meeting.conversation
        ConversationMember.objects.create(
            conversation=self.conversation, user=self.other
        )
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=GOLDEN_DAY,
            token_hash="d" * 64,
        )

        host_msg = self._message(
            minutes=0, author=self.host, body="hello from the host"
        )
        guest_msg = self._message(
            minutes=1, guest=self.guest, body="hello from a guest"
        )
        self._message(
            minutes=2,
            author=self.host,
            body="Call started",
            kind=Message.Kind.SYSTEM,
            tool_data={"type": "call", "state": "active"},
        )
        self._message(
            minutes=3, author=self.host, body="answering the guest", reply_to=guest_msg
        )
        Reaction.objects.create(message=guest_msg, user=self.host, emoji="\U0001f44d")
        PinnedMessage.objects.create(
            conversation=self.conversation, message=host_msg, pinned_by=self.host
        )
        self.client.force_login(self.host)

    def tearDown(self):
        cache.clear()

    def _message(self, *, minutes, body, author=None, guest=None, **kwargs):
        """A message at a fixed instant - created_at is auto_now_add, so the
        wall clock has to be overwritten after the insert."""
        msg = Message.objects.create(
            conversation=self.conversation,
            author=author,
            guest=guest,
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


# The controls a guest viewer is never offered, each identified by the Alpine
# handler it calls - every one of them reaches a member-only endpoint.
CAPABILITY_HANDLERS = (
    "openEmojiPicker(",
    "toggleReaction(",
    "pinMessage(",
    "unpinMessage(",
    "startEdit(",
    "deleteMessage(",
    "openThread(",
)
# The read receipt is a popover rather than a button, and it is addressed by
# conversation uuid - the other reason a guest must not get it.
RECEIPT_MARKER = "readers-popover-"

_SEPARATOR = '<div class="w-px self-stretch my-0.5 bg-base-300"></div>'
# Attachments are the one place the two panes render different content rather
# than the same content minus a control: everything the member payload points
# at is session-authenticated, so a guest is told an attachment exists. Both
# spellings collapse to this marker, which keeps their POSITION in the bubble
# under comparison.
_ATTACHMENTS = "<attachments>"
_MEMBER_ATTACHMENTS_RE = re.compile(r'<script type="application/json">.*?</script>')
_GUEST_ATTACHMENTS = (
    '<div class="text-xs italic opacity-60 my-1">Attachment shared with members</div>'
)
_RECEIPT_RE = re.compile(
    r'<span slot="footer" class="inline-flex.*?</div> </div> </span>'
)


def drop_member_only_controls(html):
    """Remove from the MEMBER render exactly what a guest viewer withholds.

    One-directional on purpose: the guest render is asserted to contain none
    of these separately, so stripping them here cannot hide guest chrome.
    """
    for handler in CAPABILITY_HANDLERS:
        html = re.sub(
            r'<button[^>]*@click="' + re.escape(handler) + r"[^>]*>.*?</button>",
            "",
            html,
        )
    html = html.replace(_SEPARATOR, "")
    return _RECEIPT_RE.sub("", html)


def drop_viewer_markers(html):
    """Normalise the attributes that name the reader rather than the content:
    the list scope, the guest marker, the `own` marker and the side class it
    drives."""
    html = re.sub(
        r'data-conversation-uuid="[^"]*"', 'data-conversation-uuid="<scope>"', html
    )
    html = html.replace("<chat-message-group own ", "<chat-message-group ")
    html = html.replace("<chat-message-group viewer-guest ", "<chat-message-group ")
    html = _MEMBER_ATTACHMENTS_RE.sub(_ATTACHMENTS, html)
    html = html.replace(_GUEST_ATTACHMENTS, _ATTACHMENTS)
    return re.sub(r"(pt-1 )(right-0|left-0)( opacity-0)", r"\1<side>\3", html)


def normalise(html, *, member):
    """The complete list of allowed differences, applied in one pass.

    The trailing collapse matters: removing a control leaves the whitespace
    that surrounded it behind, and that gap is an artefact of the removal
    rather than a difference between the two panes.
    """
    html = collapse(html)
    if member:
        html = drop_member_only_controls(html)
    return collapse(drop_viewer_markers(html))


class MeetingMessageListFixture(TestCase):
    """A meeting whose occurrence is open right now, with an admitted guest."""

    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.host = User.objects.create_user(
            username="meethost", password="pw", first_name="Hana", last_name="Host"
        )
        event = make_event(
            self.host,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        self.meeting = create_meeting(event, self.host)
        self.conversation = self.meeting.conversation
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]
        self.guest, self.token = guest_with_token(self.meeting, self.occurrence_start)
        self.guest_url = f"/meet/{self.meeting.slug}/messages"
        self.member_url = reverse(
            "chat_ui:conversation_messages",
            kwargs={"conversation_uuid": self.conversation.uuid},
        )

    def tearDown(self):
        cache.clear()

    def message(self, *, offset_minutes, body, author=None, guest=None, **kwargs):
        msg = Message.objects.create(
            conversation=self.conversation,
            author=author,
            guest=guest,
            body=body,
            **kwargs,
        )
        Message.objects.filter(pk=msg.pk).update(
            created_at=self.occurrence_start + timedelta(minutes=offset_minutes)
        )
        msg.refresh_from_db()
        return msg

    def attach(self, message, name, mime, category, viewer=""):
        return MessageAttachment.objects.create(
            message=message,
            file=SimpleUploadedFile(name, b"x", content_type=mime),
            original_name=name,
            mime_type=mime,
            category=category,
            viewer=viewer,
            size=1,
        )

    def guest_html(self, query="", token=None):
        resp = self.client.get(
            self.guest_url + query,
            HTTP_X_MEETING_TOKEN=self.token if token is None else token,
        )
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def member_html(self):
        self.client.force_login(self.host)
        resp = self.client.get(self.member_url)
        self.assertEqual(resp.status_code, 200)
        self.client.logout()
        return resp.content.decode()


class GuestMemberParityTests(MeetingMessageListFixture):
    """The two panes render the same list.

    The normalisation applied below is the complete list of differences the
    design allows: the list scope attribute, the `viewer-guest` marker, the
    `own` marker with the side class it drives, and the controls a guest has
    no endpoint for. Anything else that drifts - a class, an attribute, an
    ordering - survives normalisation and fails here.
    """

    maxDiff = None

    def setUp(self):
        super().setUp()
        self.member_msg = self.message(
            offset_minutes=1, author=self.host, body="from a member"
        )
        self.guest_msg = self.message(
            offset_minutes=2, guest=self.guest, body="from the guest"
        )
        self.system_msg = self.message(
            offset_minutes=3,
            author=self.host,
            body="Call started",
            kind=Message.Kind.SYSTEM,
            tool_data={"type": "call", "state": "active"},
        )
        self.attach(self.member_msg, "shot.png", "image/png", "image")

    def test_the_guest_list_is_the_member_list_minus_its_controls(self):
        guest = self.guest_html()
        member = self.member_html()

        for handler in (*CAPABILITY_HANDLERS, RECEIPT_MARKER):
            self.assertNotIn(handler, guest, handler)
        # The negative control: the member render really does carry the
        # controls this scenario can produce, so the removals below are
        # removing something. Unpin and thread need state this scenario has no
        # reason to build; they are pinned down in the guest-only suite.
        for handler in (
            "openEmojiPicker(",
            "toggleReaction(",
            "pinMessage(",
            "startEdit(",
            "deleteMessage(",
            RECEIPT_MARKER,
        ):
            self.assertIn(handler, member, handler)

        self.assertEqual(normalise(guest, member=False), normalise(member, member=True))

    def test_the_attachment_block_is_replaced_in_place(self):
        """The normalisation collapses two different renders to one marker, so
        this is what proves it is a substitution rather than the member
        payload simply vanishing."""
        self.assertIn('type="application/json"', self.member_html())
        self.assertIn("Attachment shared with members", self.guest_html())

    def test_reply_survives_on_both_panes(self):
        """Reply is the one control a guest keeps, so it must NOT be part of
        what the parity normalisation removes."""
        self.assertIn("startReply(", self.guest_html())
        self.assertIn("startReply(", self.member_html())

    def test_each_pane_marks_its_own_reader(self):
        guest = collapse(self.guest_html())
        member = collapse(self.member_html())

        self.assertIn("<chat-message-group own viewer-guest guest ", guest)
        self.assertIn(f'<chat-message-group own author-id="{self.host.id}"', member)
        self.assertNotIn(f'own author-id="{self.host.id}"', guest)
        self.assertNotIn("<chat-message-group own guest ", member)


class GuestMessageListViewTests(MeetingMessageListFixture):
    def test_an_unknown_token_is_404(self):
        resp = self.client.get(self.guest_url, HTTP_X_MEETING_TOKEN="nope")
        self.assertEqual(resp.status_code, 404)

    def test_a_missing_token_is_404(self):
        self.assertEqual(self.client.get(self.guest_url).status_code, 404)

    def test_another_meetings_token_is_404(self):
        other_host = User.objects.create_user(username="otherhost", password="pw")
        other_event = make_event(
            other_host,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
        )
        other_meeting = create_meeting(other_event, other_host)
        _, other_token = guest_with_token(
            other_meeting, current_occurrence(other_meeting, now=self.now)[0]
        )
        resp = self.client.get(self.guest_url, HTTP_X_MEETING_TOKEN=other_token)
        self.assertEqual(resp.status_code, 404)

    def test_the_list_is_floored_at_the_guests_occurrence(self):
        self.message(offset_minutes=-10, author=self.host, body="before the guest")
        self.message(offset_minutes=1, author=self.host, body="after the guest")
        html = self.guest_html()
        self.assertNotIn("before the guest", html)
        self.assertIn("after the guest", html)

    def test_a_quote_of_a_pre_floor_message_is_not_rendered(self):
        """The HTML half of GuestMessageSerializer's reply_to redaction: an
        in-window reply may legitimately answer a message from before the
        guest's occurrence, and the quote would hand them its body."""
        old = self.message(offset_minutes=-10, author=self.host, body="secret history")
        self.message(offset_minutes=1, author=self.host, body="answering", reply_to=old)
        html = self.guest_html()
        self.assertIn("answering", html)
        self.assertNotIn("secret history", html)
        self.assertNotIn("data-reply-author", html)

    def test_the_scope_attribute_is_the_slug_not_the_conversation(self):
        self.message(offset_minutes=1, author=self.host, body="hi")
        html = self.guest_html()
        self.assertNotIn(str(self.conversation.uuid), html)
        self.assertIn(f'data-conversation-uuid="{self.meeting.slug}"', html)

    def test_the_member_pane_still_stamps_the_conversation_uuid(self):
        self.message(offset_minutes=1, author=self.host, body="hi")
        self.assertIn(
            f'data-conversation-uuid="{self.conversation.uuid}"', self.member_html()
        )

    def test_a_members_message_carries_their_author_id_and_is_not_own(self):
        self.message(offset_minutes=1, author=self.host, body="hi")
        html = collapse(self.guest_html())
        self.assertIn(f'author-id="{self.host.id}"', html)
        self.assertNotIn("<chat-message-group own ", html)

    def test_the_guests_own_message_is_marked_own_and_guest(self):
        self.message(offset_minutes=1, guest=self.guest, body="mine")
        html = collapse(self.guest_html())
        self.assertIn("<chat-message-group own viewer-guest guest ", html)
        self.assertNotIn('author-id="None"', html)

    def test_no_control_a_guest_cannot_use_is_rendered(self):
        msg = self.message(offset_minutes=1, guest=self.guest, body="mine")
        Reaction.objects.create(message=msg, user=self.host, emoji="\U0001f44d")
        PinnedMessage.objects.create(
            conversation=self.conversation, message=msg, pinned_by=self.host
        )
        # A thread on the same message, so the replies footer is something the
        # template would draw if it were not gated.
        self.message(
            offset_minutes=2, author=self.host, body="in the thread", thread_root=msg
        )
        Message.objects.filter(pk=msg.pk).update(reply_count=1)
        html = self.guest_html()
        for handler in (*CAPABILITY_HANDLERS, RECEIPT_MARKER):
            self.assertNotIn(handler, html, handler)
        # The chips still render - reading who reacted is not a capability -
        # they just carry no toggle.
        self.assertIn("\U0001f44d", html)
        # pinned_message_ids is empty for a guest, so no pin marker either.
        self.assertNotIn('data-lucide="pin"', html)

    def test_pagination_reports_has_more_and_honours_the_cursor(self):
        created = [
            self.message(offset_minutes=index, author=self.host, body=f"m{index}")
            for index in range(1, 53)
        ]
        first_page = self.guest_html()
        self.assertIn('data-has-more="true"', first_page)
        # data-body rather than the rendered body: these rows are built
        # straight through the ORM, so nothing populated body_html.
        # 52 messages, 50 to a page: the two oldest are off the first page.
        self.assertNotIn('data-body="m1"', first_page)
        self.assertIn('data-body="m52"', first_page)

        oldest_shown = created[2]
        older = self.guest_html(f"?before={oldest_shown.uuid}")
        self.assertIn('data-has-more="false"', older)
        self.assertIn('data-body="m1"', older)
        self.assertNotIn('data-body="m52"', older)

    def test_a_guest_gets_no_attachment_payload_and_no_attachment_urls(self):
        """A guest holds a meeting token, not a session, so every
        /api/v1/chat/attachments/<uuid> the shell would build from the payload
        is a request they cannot make. They are told an attachment exists
        instead of being shown a mosaic of broken images."""
        msg = self.message(offset_minutes=1, author=self.host, body="see this")
        self.attach(msg, "shot.png", "image/png", "image")
        self.attach(msg, "note.webm", "video/webm", "video", viewer="audio")

        guest = self.guest_html()
        self.assertNotIn("/api/v1/chat/attachments/", guest)
        self.assertNotIn('type="application/json"', guest)
        self.assertIn("Attachment shared with members", guest)

        member = self.member_html()
        self.assertIn('type="application/json"', member)
        self.assertIn('"is_image": true', member)
        self.assertIn("/api/v1/chat/attachments/", member)
        self.assertNotIn("Attachment shared with members", member)

    def test_a_cursor_below_the_floor_is_ignored(self):
        buried = self.message(offset_minutes=-10, author=self.host, body="buried")
        self.message(offset_minutes=1, author=self.host, body="visible")
        html = self.guest_html(f"?before={buried.uuid}")
        # Ignored, not honoured: honouring it would return an empty page.
        self.assertIn("visible", html)

    def test_a_malformed_cursor_is_ignored(self):
        self.message(offset_minutes=1, author=self.host, body="visible")
        self.assertIn("visible", self.guest_html("?before=not-a-uuid"))
