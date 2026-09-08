"""Containment audit: a guest learns nothing beyond their own meeting.

A guest holds an opaque token and reaches nine routes under
``/api/v1/chat/meet/<slug>/``, plus the meeting page at ``/meetings/<slug>``.
Three things they see there are deliberate and stay in: the meeting's own
title, the meeting's own chat of their occurrence, and the participant keys
and display names of the members they are in a call with. Everything else
that could reach them - another meeting, a conversation of any kind, an email
address, a foreign uuid - is a leak.

That property is invisible to the per-route behavioural suites, which each
assert on the one field they are about. So this module builds a second,
FOREIGN world next to the meeting under test (a control user who shares a
conversation with the host, a control conversation, a control meeting with its
own event, slug, admitted guest and chat), gives every string in it a sentinel
spelling, then walks every guest-reachable route and every SSE frame and fails
on the whole response text rather than on a named field. A leak through a
field nobody thought to check still carries one of those sentinels.

The last class pins the four structural properties the runtime rules depend
on - which views are anonymous, that no service imports a view, that no public
path calls the self-healing ``get_active_call``, and that the meeting chat has
exactly one serializer - by reading the source, so the baseline cannot drift
silently.
"""

import ast
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIClient

import workspace.chat
from workspace.calendar.models import EventMember
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    MeetingGuest,
    MeetingMessage,
    Message,
)
from workspace.chat.services import calls
from workspace.chat.services.call_signaling import enqueue_event
from workspace.chat.services.meeting_messages import post_message, serialize_message
from workspace.chat.services.meeting_occurrences import current_occurrence
from workspace.chat.services.meetings import admit_guest, create_meeting
from workspace.chat.services.participant_keys import guest_key, user_key

from .meeting_fixtures import (
    FakeClock,
    StopDriving,
    drive_guest_stream,
    guest_with_token,
    make_event,
    parse_sse,
)

User = get_user_model()

CHAT_DIR = Path(workspace.chat.__file__).resolve().parent
VIEWS_DIR = CHAT_DIR / "views"
SERVICES_DIR = CHAT_DIR / "services"
UI_VIEWS = CHAT_DIR / "ui" / "views.py"

# Distinctive spellings, so a leak is identifiable by the value alone rather
# than by which assertion happened to catch it.
SENTINEL_CONVERSATION_TITLE = "Sentinel-Control-Conversation"
SENTINEL_MEETING_TITLE = "Sentinel-Control-Meeting"
SENTINEL_CALENDAR_NAME = "Sentinel-Control-Calendar"
SENTINEL_MESSAGE_BODY = "Sentinel-Control-Message-Body"
SENTINEL_MEETING_LINE = "Sentinel-Control-Meeting-Line"
SENTINEL_GUEST_NAME = "Sentinel-Control-Guest"
FOREIGN_USERNAME = "zz_foreign_member"
EMAIL_DOMAIN = "sentinel.example"


class GuestContainmentFixture(TestCase):
    """One meeting a guest legitimately belongs to, and a foreign world they
    must never reach, plus the assertion that separates the two."""

    def setUp(self):
        cache.clear()
        self.now = timezone.now()
        self.client = APIClient()

        self.host = self._user("meeting_host", first_name="Hana", last_name="Host")
        self.member = self._user("meeting_member", first_name="Mo", last_name="Member")
        # Neither host nor part of the foreign world: the signed-in stranger
        # the knock endpoint binds an account to.
        self.outsider = self._user(
            "meeting_outsider", first_name="Ola", last_name="Outsider"
        )
        self.event = make_event(
            self.host,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
            title="Weekly sync",
        )
        # Hosts are derived from the event, so a co-host is an invitee.
        EventMember.objects.create(event=self.event, user=self.member)
        self.meeting = create_meeting(self.event, self.host)
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]
        self.guest, self.token = guest_with_token(
            self.meeting, self.occurrence_start, display_name="Ada"
        )
        self.host_message = post_message(
            self.meeting, "host says hi", author=self.host, now=self.now
        )

        self._build_foreign_world()

    def tearDown(self):
        cache.clear()

    def _user(self, username, **names):
        return User.objects.create_user(
            username=username,
            email=f"{username}@{EMAIL_DOMAIN}",
            password="x",
            **names,
        )

    def _build_foreign_world(self):
        """Everything a naive query could drag in: a user who shares a
        conversation with the host, and a whole second meeting with its own
        chat."""
        self.foreign_user = self._user(
            FOREIGN_USERNAME, first_name="Zoe", last_name="Sentinel-Foreign"
        )
        self.control_conversation = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title=SENTINEL_CONVERSATION_TITLE,
            created_by=self.host,
        )
        ConversationMember.objects.bulk_create(
            [
                ConversationMember(
                    conversation=self.control_conversation, user=self.host
                ),
                ConversationMember(
                    conversation=self.control_conversation, user=self.foreign_user
                ),
            ]
        )
        self.control_message = Message.objects.create(
            conversation=self.control_conversation,
            author=self.foreign_user,
            body=SENTINEL_MESSAGE_BODY,
        )

        self.control_event = make_event(
            self.foreign_user,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
            title=SENTINEL_MEETING_TITLE,
            calendar_name=SENTINEL_CALENDAR_NAME,
        )
        # The host runs the control meeting too, so "the meetings of this
        # meeting's host" is not a query that could accidentally look scoped
        # while surfacing the control meeting.
        EventMember.objects.create(event=self.control_event, user=self.host)
        self.control_meeting = create_meeting(self.control_event, self.foreign_user)
        self.control_occurrence_start = current_occurrence(
            self.control_meeting, now=self.now
        )[0]
        self.control_guest, self.control_token = guest_with_token(
            self.control_meeting,
            self.control_occurrence_start,
            display_name=SENTINEL_GUEST_NAME,
        )
        self.control_meeting_message = post_message(
            self.control_meeting,
            SENTINEL_MEETING_LINE,
            author=self.foreign_user,
            now=self.now,
        )

        self.sentinels = [
            SENTINEL_CONVERSATION_TITLE,
            # Also the control meeting's own title: Meeting.title is copied
            # from the event at creation, so one spelling covers both rows.
            SENTINEL_MEETING_TITLE,
            SENTINEL_CALENDAR_NAME,
            SENTINEL_MESSAGE_BODY,
            SENTINEL_MEETING_LINE,
            SENTINEL_GUEST_NAME,
            FOREIGN_USERNAME,
            "Zoe Sentinel-Foreign",
            self.control_meeting.slug,
            self.control_token,
            str(self.control_meeting.uuid),
            str(self.control_conversation.uuid),
            str(self.control_event.uuid),
            str(self.control_guest.uuid),
            str(self.control_message.uuid),
            str(self.control_meeting_message.uuid),
            # A mention badge naming a user outside this meeting: the meeting
            # chat renders no mention map at all, so this pairing can never
            # legitimately render.
            f'data-user-id="{self.foreign_user.id}"',
        ]

    # --- the audit itself ---

    def assert_contained(self, label, text, echo=()):
        """Fail if *text* carries anything from outside the guest's meeting.

        Case-insensitive: a uuid re-spelled in upper case is the same
        disclosure. Emails and conversation ids are re-read from the database
        on every call rather than snapshotted in setUp, so a row a test
        creates on its own is covered too.

        *echo* is text the caller submitted in that same request: a route
        that repeats a guest's own input back cannot disclose anything by
        doing so, and it is stripped from the haystack first. What the
        server *adds* around it stays in - a mention badge's
        ``data-user-id`` is its own sentinel.
        """
        haystack = text.lower()
        for submitted in echo:
            haystack = haystack.replace(submitted.lower(), "")

        for sentinel in self.sentinels:
            self.assertNotIn(
                sentinel.lower(), haystack, f"{label} leaked sentinel {sentinel!r}"
            )

        for email in User.objects.exclude(email="").values_list("email", flat=True):
            self.assertNotIn(
                email.lower(), haystack, f"{label} leaked the address {email!r}"
            )

        # A meeting owns no conversation, so every conversation row in the
        # database is foreign to a guest, without exception to carve out.
        for conversation_id in Conversation.objects.values_list("uuid", flat=True):
            self.assertNotIn(
                str(conversation_id).lower(),
                haystack,
                f"{label} leaked conversation {conversation_id}",
            )

    def body_text(self, response):
        """The whole response, raw and re-serialized, so nested fields count."""
        raw = response.content.decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except ValueError:
            return raw
        return raw + "\n" + json.dumps(parsed, ensure_ascii=False, default=str)

    def url(self, action=""):
        base = f"/api/v1/chat/meet/{self.meeting.slug}"
        return f"{base}/{action}" if action else base

    def host_url(self, action=""):
        base = f"/api/v1/chat/meetings/{self.meeting.uuid}"
        return f"{base}/{action}" if action else base

    def drain_stream(self, token):
        """(response, frames) for the SSE view, bounded to a single cycle.

        The patched sleep raises out of the generator on the first cycle
        boundary, so a regression that never terminates fails here instead of
        hanging on a real 1s wait.
        """
        response = self.client.get(self.url("stream"), HTTP_X_MEETING_TOKEN=token)
        frames = []

        def _stop(_seconds):
            raise StopDriving

        with patch(
            "workspace.chat.services.guest_stream.time.sleep", side_effect=_stop
        ):
            content_iter = response.streaming_content
            try:
                while True:
                    frames.append(next(content_iter).decode("utf-8"))
            except StopIteration, StopDriving:
                pass
        return response, frames


class GuestRouteContainmentTests(GuestContainmentFixture):
    def test_every_guest_route_answers_without_leaking(self):
        """The whole reachable surface, walked in one go with a valid token.

        Each route is exercised with a payload a real client would send and
        must answer successfully - an empty 404 body would pass containment
        while proving nothing.
        """
        calls.start_or_join_call(self.host, calls.MeetingScope(self.meeting))
        media_state = {"audio": True, "video": False, "screen": False}
        header = {"HTTP_X_MEETING_TOKEN": self.token}
        signed_in = APIClient()
        signed_in.force_login(self.outsider)

        walk = [
            ("GET summary", 200, self.client.get(self.url())),
            (
                "POST knock",
                201,
                self.client.post(
                    self.url("knock"), {"display_name": "Bo"}, format="json"
                ),
            ),
            (
                # The same door with a session behind it: knocking binds the
                # visitor's account to the guest row, so this response
                # carries an identity the anonymous one has nothing to carry.
                "POST knock signed in",
                201,
                signed_in.post(
                    self.url("knock"), {"display_name": "Bo"}, format="json"
                ),
            ),
            (
                "POST join",
                200,
                self.client.post(
                    self.url("join"),
                    {"media_state": media_state},
                    format="json",
                    **header,
                ),
            ),
            (
                "POST signal",
                200,
                self.client.post(
                    self.url("signal"),
                    {
                        "to_participant": user_key(self.host.id),
                        "signal": {"type": "offer", "sdp": "v=0"},
                    },
                    format="json",
                    **header,
                ),
            ),
            (
                "POST messages",
                201,
                self.client.post(
                    self.url("messages"),
                    {"body": f"hello from the guest @{FOREIGN_USERNAME}"},
                    format="json",
                    **header,
                ),
                (f"@{FOREIGN_USERNAME}",),
            ),
            ("GET state", 200, self.client.get(self.url("state"), **header)),
            (
                "POST heartbeat",
                200,
                self.client.post(
                    self.url("heartbeat"),
                    {"media_state": media_state},
                    format="json",
                    **header,
                ),
            ),
            (
                "GET messages",
                200,
                self.client.get(f"{self.url('messages')}?limit=50", **header),
                (f"@{FOREIGN_USERNAME}",),
            ),
            (
                # The page itself, fetched by a stranger with no token at
                # all: it is the document every route above is loaded from.
                "GET meeting page",
                200,
                self.client.get(self.meeting.join_path),
            ),
        ]

        for entry in walk:
            label, expected_status, response = entry[:3]
            echo = entry[3] if len(entry) > 3 else ()
            with self.subTest(route=label):
                self.assertEqual(response.status_code, expected_status, label)
                self.assert_contained(label, self.body_text(response), echo=echo)

        stream_response, frames = self.drain_stream(self.token)
        self.assertEqual(stream_response.status_code, 200)
        self.assert_contained("GET stream", "".join(frames))

        leave = self.client.post(self.url("leave"), **header)
        self.assertEqual(leave.status_code, 200)
        self.assert_contained("POST leave", self.body_text(leave))

    def test_no_message_surface_names_the_account_behind_a_host(self):
        """Containment over the avatar: the meeting chat is shared, the
        workspace identities behind it are not.

        Targeted rather than a sentinel on ``self.sentinels``: the host is a
        legitimate part of this guest's world (their display name is on every
        line they wrote, their participant key drives the call tiles), so only
        the two account fields are audited, and only where a message payload
        carries them.
        """
        header = {"HTTP_X_MEETING_TOKEN": self.token}
        posted = self.client.post(
            self.url("messages"), {"body": "hello"}, format="json", **header
        )
        self.assertEqual(posted.status_code, 201)
        listed = self.client.get(self.url("messages"), **header)
        self.assertEqual(listed.status_code, 200)

        payloads = [posted.json()] + listed.json()["messages"]
        _stream, frames = self.drain_stream(self.token)
        for frame in frames:
            if frame.startswith(":"):
                continue
            name, data = parse_sse(frame)
            if name == "meeting_message":
                payloads.append(data["message"])

        # The positive control: a host-authored line really is in there, so a
        # regression cannot pass by returning nothing.
        self.assertIn(self.host_message.body, [p["body"] for p in payloads])
        for payload in payloads:
            author = payload["author"]
            self.assertNotIn("id", author, author)
            self.assertNotIn("username", author, author)
            text = json.dumps(payload, ensure_ascii=False, default=str)
            self.assertNotIn(self.host.username, text)
            self.assertNotIn(f'"id": {self.host.id}', text)

    def test_the_host_api_of_this_meeting_refuses_the_token(self):
        """A meeting token authorizes a guest, never a host.

        The host routes carry ``IsAuthenticated``, and the token is not an
        authentication credential to them - so every one of them must refuse,
        including the ones addressed by this guest's own meeting uuid.
        """
        header = {"HTTP_X_MEETING_TOKEN": self.token}
        guest_path = f"guests/{self.guest.uuid}"
        post = self.client.post

        attempts = [
            ("GET lobby", self.host_url("lobby"), self.client.get),
            ("POST admit", self.host_url(f"{guest_path}/admit"), post),
            ("POST refuse", self.host_url(f"{guest_path}/refuse"), post),
            ("POST remove", self.host_url(f"{guest_path}/remove"), post),
            ("POST lock", self.host_url("lock"), post),
            ("POST end", self.host_url("end"), post),
            ("GET call", self.host_url("call"), self.client.get),
            ("POST call join", self.host_url("call/join"), post),
            ("POST call leave", self.host_url("call/leave"), post),
            ("POST call heartbeat", self.host_url("call/heartbeat"), post),
            ("POST call signal", self.host_url("call/signal"), post),
            ("GET host messages", self.host_url("messages"), self.client.get),
            ("POST host messages", self.host_url("messages"), post),
            (
                "DELETE host message",
                self.host_url(f"messages/{self.host_message.uuid}"),
                self.client.delete,
            ),
        ]

        for label, path, method in attempts:
            with self.subTest(route=label):
                # Resolved first: a path this file spells wrong would 404 and
                # sail through the refusal check below having exercised no
                # view at all.
                resolve(path)
                response = method(path, **header)
                self.assertIn(response.status_code, (401, 403, 404), label)
                self.assert_contained(label, self.body_text(response))

        self.assertTrue(
            MeetingMessage.objects.filter(uuid=self.host_message.uuid).exists()
        )

    def test_the_guests_own_meeting_is_still_visible(self):
        """The negative control for the walk above: containment must not be
        passing because the routes return nothing at all."""
        summary = self.client.get(self.url())
        self.assertEqual(summary.json()["title"], self.event.title)

        listing = self.client.get(self.url("messages"), HTTP_X_MEETING_TOKEN=self.token)
        bodies = [m["body"] for m in listing.json()["messages"]]
        self.assertIn(self.host_message.body, bodies)

    def test_another_meetings_token_reaches_nothing_here(self):
        """A token is scoped to its own meeting's slug, not to any slug.

        Summary and knock are excluded on purpose: both are anonymous and
        slug-addressed, so they never read the header and answer the same way
        for everyone.
        """
        calls.start_or_join_call(self.host, calls.MeetingScope(self.meeting))
        header = {"HTTP_X_MEETING_TOKEN": self.control_token}

        attempts = [
            (
                "POST join",
                self.client.post(self.url("join"), {}, format="json", **header),
            ),
            ("POST leave", self.client.post(self.url("leave"), **header)),
            (
                "POST heartbeat",
                self.client.post(
                    self.url("heartbeat"),
                    {"media_state": {"audio": True}},
                    format="json",
                    **header,
                ),
            ),
            ("GET state", self.client.get(self.url("state"), **header)),
            (
                "POST signal",
                self.client.post(
                    self.url("signal"),
                    {
                        "to_participant": user_key(self.host.id),
                        "signal": {"type": "offer"},
                    },
                    format="json",
                    **header,
                ),
            ),
            ("GET messages", self.client.get(self.url("messages"), **header)),
            (
                "POST messages",
                self.client.post(
                    self.url("messages"),
                    {"body": "should never land"},
                    format="json",
                    **header,
                ),
            ),
            ("GET stream", self.client.get(self.url("stream"), **header)),
        ]

        for label, response in attempts:
            with self.subTest(route=label):
                self.assertEqual(response.status_code, 404, label)
                self.assert_contained(label, self.body_text(response))

        self.assertFalse(
            MeetingMessage.objects.filter(guest=self.control_guest).exists()
        )


class GuestStreamContainmentTests(GuestContainmentFixture):
    def test_the_admitted_guests_stream_carries_nothing_foreign(self):
        """Every SSE frame an admitted guest receives, held to the same bar as
        a REST body - the stream is the only guest surface that pushes without
        being asked."""
        calls.start_or_join_call(self.host, calls.MeetingScope(self.meeting))
        calls.join_call_as_guest(self.guest)
        # A second host joining is a real fan-out that reaches the guest's
        # mailbox, rather than an event enqueued by hand.
        calls.start_or_join_call(self.member, calls.MeetingScope(self.meeting))

        clock = FakeClock(self.now, max_cycles=1)
        frames, _terminated = drive_guest_stream(self.token, self.meeting.uuid, clock)
        names = [parse_sse(f)[0] for f in frames if not f.startswith(":")]

        self.assertIn("meeting_message", names)
        self.assertTrue(any(name.startswith("call_") for name in names), names)
        self.assert_contained("guest stream", "".join(frames))

    def test_a_line_posted_mid_stream_reaches_the_guest_contained(self):
        """The meeting's own chat is the one content stream a guest is pushed
        without asking: a line posted while they hold the connection open
        lands on the next cycle, carrying its author and nothing else. Posted
        mid-stream rather than before it opens, so the frame under audit is
        the one the fan-out produced, not a mailbox leftover."""
        calls.start_or_join_call(self.host, calls.MeetingScope(self.meeting))
        calls.join_call_as_guest(self.guest)

        def _post_to_both_meetings():
            post_message(
                self.meeting, "posted mid-stream", author=self.host, now=self.now
            )
            # The same instant in the control meeting: its fan-out reaches
            # the control meeting's own guest, never this one.
            post_message(
                self.control_meeting,
                SENTINEL_MEETING_LINE,
                author=self.foreign_user,
                now=self.now,
            )

        clock = _CallbackClock(self.now, _post_to_both_meetings, max_cycles=2)
        frames, _terminated = drive_guest_stream(self.token, self.meeting.uuid, clock)
        parsed = [parse_sse(f) for f in frames if not f.startswith(":")]
        bodies = [
            data["message"]["body"]
            for name, data in parsed
            if name == "meeting_message"
        ]

        self.assertIn("posted mid-stream", bodies)
        self.assert_contained("mid-stream meeting_message", "".join(frames))

    def test_a_waiting_guest_receives_only_lifecycle_events(self):
        """The lobby is not a preview: until the host admits them, a guest's
        open stream carries the four meeting_* events and nothing else, however
        much is flowing through the meeting."""
        waiting_guest, waiting_token = guest_with_token(
            self.meeting,
            self.occurrence_start,
            display_name="Wendy",
            state=MeetingGuest.State.WAITING,
        )
        calls.start_or_join_call(self.host, calls.MeetingScope(self.meeting))
        line = post_message(self.meeting, "members only, for now", author=self.host)
        key = guest_key(waiting_guest.uuid)
        # meeting_admitted is the positive control: without it the test would
        # also pass if nothing at all were being drained.
        enqueue_event(key, "meeting_admitted", {"meeting_id": str(self.meeting.uuid)})
        # post_message fans out to admitted guests only, so the message half
        # of the gate is exercised by putting the frame in the mailbox by
        # hand - what a fan-out regression would do - rather than trusting
        # the fan-out to have already withheld it.
        enqueue_event(
            key,
            "meeting_message",
            {
                "meeting_id": str(self.meeting.uuid),
                "message": serialize_message(line),
            },
        )
        enqueue_event(
            key,
            "call_started",
            {"session_id": "x", "meeting_id": str(self.meeting.uuid)},
        )
        enqueue_event(
            key,
            "call_participant_joined",
            {"session_id": "x", "participant_key": user_key(self.host.id)},
        )

        clock = FakeClock(self.now, max_cycles=1)
        frames, _terminated = drive_guest_stream(
            waiting_token, self.meeting.uuid, clock
        )
        names = [parse_sse(f)[0] for f in frames if not f.startswith(":")]

        self.assertEqual(names, ["meeting_admitted"])
        self.assert_contained("waiting guest stream", "".join(frames))

    def test_admission_opens_the_stream_the_lobby_kept_shut(self):
        """The other half of the fence above: once the host admits them, the
        very next cycle forwards the messages and the call events the lobby
        withheld."""
        waiting_guest, waiting_token = guest_with_token(
            self.meeting,
            self.occurrence_start,
            display_name="Wendy",
            state=MeetingGuest.State.WAITING,
        )
        calls.start_or_join_call(self.host, calls.MeetingScope(self.meeting))
        key = guest_key(waiting_guest.uuid)
        enqueue_event(key, "call_started", {"session_id": "x"})

        def _admit_and_fan_out():
            admit_guest(waiting_guest, self.host)
            post_message(self.meeting, "posted once the guest was in", author=self.host)
            enqueue_event(
                key,
                "call_participant_joined",
                {"session_id": "x", "participant_key": user_key(self.member.id)},
            )

        clock = _CallbackClock(self.now, _admit_and_fan_out, max_cycles=2)
        frames, _terminated = drive_guest_stream(
            waiting_token, self.meeting.uuid, clock
        )
        names = [parse_sse(f)[0] for f in frames if not f.startswith(":")]

        self.assertIn("meeting_admitted", names)
        self.assertIn("meeting_message", names)
        self.assertIn("call_participant_joined", names)
        # Drained and dropped while the guest was still waiting: forwarding is
        # not retroactive.
        self.assertNotIn("call_started", names)
        self.assert_contained("admitted guest stream", "".join(frames))


class _CallbackClock(FakeClock):
    """A FakeClock that runs *callback* once, between cycle one and cycle two,
    so a test can change the world mid-stream the way a host would."""

    def __init__(self, start, callback, **kwargs):
        super().__init__(start, **kwargs)
        self._callback = callback

    def sleep(self, seconds):
        if self.cycles == 0:
            self._callback()
        super().sleep(seconds)


def _parse_module(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _called_name(func):
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _calls_to(tree, name):
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node.func) == name
    ]


def _class_named(tree, name):
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _is_empty_list(value):
    """``[]`` and ``list()`` are the same emptiness to DRF, so they are the
    same emptiness here."""
    if isinstance(value, ast.List):
        return not value.elts
    return (
        isinstance(value, ast.Call)
        and _called_name(value.func) == "list"
        and not value.args
        and not value.keywords
    )


def _has_empty_authentication_classes(class_node):
    """True when *class_node* empties ``authentication_classes``, however it
    spells it.

    An annotated assignment (``authentication_classes: list = []``) is an
    ``AnnAssign``, not an ``Assign``, and a checker that reads only the latter
    would let a view spelled that way answer strangers while never appearing
    in the enumerated set below - the one failure mode this whole check
    exists to prevent.
    """
    for statement in class_node.body:
        if isinstance(statement, ast.AnnAssign):
            targets, value = [statement.target], statement.value
        elif isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        else:
            continue
        if value is None or not _is_empty_list(value):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "authentication_classes"
            for target in targets
        ):
            return True
    return False


def _body_html_dict_builders(tree):
    """Scopes in *tree* that construct a dict carrying a ``body_html`` key.

    A function is named by its own name; a dict built outside any function is
    reported as ``<module>``. Walked scope by scope rather than with
    ``ast.walk``, which flattens the tree and loses the enclosing function -
    the very thing the fence below has to name.
    """
    found = set()

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                visit(child, child.name)
                continue
            if isinstance(child, ast.Dict) and any(
                isinstance(key, ast.Constant) and key.value == "body_html"
                for key in child.keys
            ):
                found.add(scope)
            visit(child, scope)

    visit(tree, "<module>")
    return found


# workspace.chat.views, named either absolutely or from inside the chat package.
_VIEW_PACKAGES = ("workspace.chat.views", "chat.views")
_CHAT_PACKAGES = ("workspace.chat", "chat")


def _imports_a_view(node):
    """True when *node* pulls anything out of ``workspace.chat.views``.

    Four spellings, not one. The module can be named outright
    (``from workspace.chat.views import x``, ``from ..views import x``), or the
    package can be hopped to and ``views`` imported as a plain name
    (``from .. import views``, ``from workspace.chat import views``). The
    second pair leaves ``node.module`` naming the *package*, so a check that
    reads only ``module`` waves it through - and a later ``views.meetings.x()``
    is exactly the backwards layering this is here to catch.
    """
    if isinstance(node, ast.Import):
        return any(
            alias.name == package or alias.name.startswith(f"{package}.")
            for alias in node.names
            for package in _VIEW_PACKAGES
        )
    if not isinstance(node, ast.ImportFrom):
        return False

    module = node.module or ""
    if node.level == 0:
        if any(
            module == package or module.startswith(f"{package}.")
            for package in _VIEW_PACKAGES
        ):
            return True
    elif module == "views" or module.startswith("views."):
        return True

    hops_to_chat_package = (node.level >= 2 and not node.module) or (
        module in _CHAT_PACKAGES
    )
    return hops_to_chat_package and any(alias.name == "views" for alias in node.names)


def _imports_of(tree, name):
    """Every ``from ... import`` in *tree* that binds *name*, alias or not."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == name for alias in node.names)
    ]


def _aliased_imports_of(tree, name):
    """Only the ones that rebind *name* to something else."""
    return [
        node
        for node in _imports_of(tree, name)
        if any(alias.name == name and alias.asname for alias in node.names)
    ]


def _decorator_name(node):
    """The bare name of a decorator, applied with or without arguments."""
    if isinstance(node, ast.Call):
        node = node.func
    return node.id if isinstance(node, ast.Name) else None


class GuestSurfaceSourceTests(SimpleTestCase):
    """The structural invariants the runtime rules rest on, read off the
    source so a change to any of them fails here rather than in production."""

    # Every view that answers without authenticating anyone. Emptying
    # authentication_classes is what makes a route reachable by a stranger, so
    # the set is enumerated rather than counted: a tenth anonymous view is
    # a decision, not an accident.
    ANONYMOUS_VIEWS = {
        "avatar.py": {"GroupAvatarRetrieveView"},
        "meetings.py": {"MeetingSummaryView"},
        "meeting_guest.py": {
            "MeetingGuestJoinView",
            "MeetingGuestLeaveView",
            "MeetingGuestHeartbeatView",
            "MeetingGuestSignalView",
            "MeetingGuestStateView",
            "MeetingGuestStreamView",
            "MeetingGuestMessagesView",
        },
    }

    def test_the_anonymous_views_are_exactly_the_known_set(self):
        """No view becomes reachable without authentication unnoticed."""
        found = {}
        for path in sorted(VIEWS_DIR.glob("*.py")):
            classes = {
                node.name
                for node in ast.walk(_parse_module(path))
                if isinstance(node, ast.ClassDef)
                and _has_empty_authentication_classes(node)
            }
            if classes:
                found[path.name] = classes

        self.assertEqual(found, self.ANONYMOUS_VIEWS)
        self.assertEqual(sum(len(names) for names in found.values()), 9)

    # The UI half of the same fence. The API views above are enumerated by
    # what empties authentication_classes; a page view is enumerated by what
    # it is missing instead - @login_required - because that decorator is the
    # only thing standing between a template and a stranger. Both entries are
    # the meeting page: the document itself, and the first link's redirect
    # onto it.
    ANONYMOUS_UI_VIEWS = {"meeting_view", "meet_redirect_view"}

    def _ui_view_decorators(self):
        """{view name: decorator names} for every view in chat/ui/views.py.

        A view is what Django calls with a request; the module's other
        top-level functions are helpers that take a user, a message or the
        meeting they build a payload for.

        ``AsyncFunctionDef`` is not a variant to skip: Django routes an async
        view exactly like a sync one, so a fence that only walks
        ``FunctionDef`` would let ``async def meet_something(request)`` ship
        unseen by both tests below - which is the failure mode they exist to
        prevent.
        """
        decorators = {}
        for node in _parse_module(UI_VIEWS).body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not node.args.args or node.args.args[0].arg != "request":
                continue
            decorators[node.name] = {
                name
                for name in (
                    _decorator_name(decorator) for decorator in node.decorator_list
                )
                if name is not None
            }
        return decorators

    def test_the_anonymous_ui_views_are_exactly_the_known_set(self):
        """A second public page is a decision, not a forgotten decorator."""
        found = {
            name
            for name, decorators in self._ui_view_decorators().items()
            if "login_required" not in decorators
        }

        self.assertEqual(found, self.ANONYMOUS_UI_VIEWS)

    def test_every_anonymous_ui_view_is_rate_limited(self):
        """The DRF endpoints beside these pages carry MeetingPublicIpThrottle;
        a plain Django view has no throttle machinery in front of it and gets
        one only from this decorator. Enumerated off the source so a third
        public page cannot ship unthrottled."""
        unlimited = {
            name
            for name, decorators in self._ui_view_decorators().items()
            if "login_required" not in decorators
            and "meeting_public_ip_limited" not in decorators
        }

        self.assertEqual(unlimited, set())

    def test_no_service_imports_a_view(self):
        """Services stay callable from a view, a task or a command alike; a
        service reaching back into ``views`` would drag a request-shaped,
        permission-checked layer into paths that have neither."""
        offenders = []
        for path in sorted(SERVICES_DIR.rglob("*.py")):
            offenders.extend(
                f"{path.name}:{node.lineno}"
                for node in ast.walk(_parse_module(path))
                if _imports_a_view(node)
            )

        self.assertEqual(offenders, [])

    def test_no_public_view_calls_get_active_call(self):
        """``get_active_call`` self-heals: it takes ``select_for_update``, can
        end a stale session and broadcasts. An anonymous caller must never
        drive that, so the public paths read the call through
        ``is_call_locked`` / ``active_call_session_for_guest`` instead."""
        guest_views = _parse_module(VIEWS_DIR / "meeting_guest.py")
        self.assertEqual(_calls_to(guest_views, "get_active_call"), [])
        # Nothing in this file may even reach the name: matching call sites by
        # name alone is blind to ``import get_active_call as gac``, and every
        # class here is anonymous, so there is no legitimate importer.
        self.assertEqual(_imports_of(guest_views, "get_active_call"), [])

        meetings = _parse_module(VIEWS_DIR / "meetings.py")
        # The host views in this file may read the call; only the rename is
        # refused, since it is what would hide a call from the check below.
        self.assertEqual(_aliased_imports_of(meetings, "get_active_call"), [])
        public_classes = [
            node
            for node in ast.walk(meetings)
            if isinstance(node, ast.ClassDef)
            and _has_empty_authentication_classes(node)
        ]
        self.assertEqual(
            {node.name for node in public_classes},
            self.ANONYMOUS_VIEWS["meetings.py"],
        )
        for node in public_classes:
            self.assertEqual(_calls_to(node, "get_active_call"), [], node.name)

        # MeetingKnockView keeps DRF's default authentication so a signed-in
        # visitor is bound to the guest row, which takes it out of the set
        # above - but a stranger still reaches it with no credentials at all,
        # so the rule holds for it by name.
        knock = _class_named(meetings, "MeetingKnockView")
        self.assertEqual(_calls_to(knock, "get_active_call"), [])

    def test_the_meeting_chat_paths_call_no_message_serializer(self):
        """``MessageSerializer`` is the conversation's, and it emits
        ``conversation_id`` and hydrates ``reply_to``/``thread_root``. The
        meeting chat is a different model with a different audience: its
        payload is built by ``serialize_message`` alone, so a serializer
        reached from any of these files is a conversation payload crossing
        into a meeting."""
        used = set()
        for path in (
            VIEWS_DIR / "meetings.py",
            VIEWS_DIR / "meeting_guest.py",
            VIEWS_DIR / "meeting_messages.py",
            SERVICES_DIR / "guest_stream.py",
            SERVICES_DIR / "meeting_messages.py",
        ):
            for node in ast.walk(_parse_module(path)):
                if not isinstance(node, ast.Call):
                    continue
                name = _called_name(node.func)
                if name and name.endswith("MessageSerializer"):
                    used.add(name)

        self.assertEqual(used, set())

    def test_serialize_message_is_the_only_meeting_message_payload(self):
        """The other half of the fence above: one serializer is only a fence
        while it is the only one. A second function assembling the same
        payload by hand would be free to add a field - the guest audience is
        what ``serialize_message`` was shaped around - so the ``body_html``
        key is claimed for it across the whole services package."""
        builders = set()
        for path in sorted(SERVICES_DIR.rglob("*.py")):
            builders.update(
                f"{path.name}:{scope}"
                for scope in _body_html_dict_builders(_parse_module(path))
            )

        self.assertEqual(builders, {"meeting_messages.py:serialize_message"})
