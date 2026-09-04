"""Containment audit: a guest learns nothing beyond their own meeting.

A guest holds an opaque token and reaches nine routes under
``/api/v1/chat/meet/<slug>/``. Three things they see there are deliberate and
stay in: the meeting's own title, the real conversation of that meeting floored
at their occurrence, and the participant keys and display names of the members
they are in a call with. Everything else that could reach them - another
conversation, another meeting, an email address, a foreign uuid - is a leak.

That property is invisible to the per-route behavioural suites, which each
assert on the one field they are about. So this module builds a second,
FOREIGN world next to the meeting under test (a control user who shares a
conversation with the host, a control conversation, a control meeting with its
own event, slug, conversation and admitted guest), gives every string in it a
sentinel spelling, then walks every guest-reachable route and every SSE frame
and fails on the whole response text rather than on a named field. A leak
through a field nobody thought to check still carries one of those sentinels.

The last class pins the four structural properties the runtime rules depend
on - which views are anonymous, that no service imports a view, that no public
path calls the self-healing ``get_active_call``, and that guest-facing code
serializes messages only through ``GuestMessageSerializer`` - by reading the
source, so the baseline cannot drift silently.
"""

import ast
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.utils import timezone
from rest_framework.test import APIClient

import workspace.chat
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    MeetingGuest,
    Message,
)
from workspace.chat.services import calls
from workspace.chat.services.call_signaling import enqueue_event
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

# Distinctive spellings, so a leak is identifiable by the value alone rather
# than by which assertion happened to catch it.
SENTINEL_CONVERSATION_TITLE = "Sentinel-Control-Conversation"
SENTINEL_MEETING_TITLE = "Sentinel-Control-Meeting"
SENTINEL_CALENDAR_NAME = "Sentinel-Control-Calendar"
SENTINEL_MESSAGE_BODY = "Sentinel-Control-Message-Body"
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
        self.event = make_event(
            self.host,
            start=self.now - timedelta(minutes=5),
            end=self.now + timedelta(minutes=25),
            title="Weekly sync",
        )
        self.meeting = create_meeting(self.event, self.host)
        ConversationMember.objects.create(
            conversation=self.meeting.conversation, user=self.member
        )
        self.occurrence_start = current_occurrence(self.meeting, now=self.now)[0]
        self.guest, self.token = guest_with_token(
            self.meeting, self.occurrence_start, display_name="Ada"
        )
        self.host_message = Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.host,
            body="host says hi",
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
        conversation with the host, and a whole second meeting."""
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
        self.control_meeting = create_meeting(self.control_event, self.foreign_user)
        # The host is a member of the control meeting too, so "the meetings of
        # this meeting's host" is not a query that could accidentally look
        # scoped while surfacing the control meeting.
        ConversationMember.objects.create(
            conversation=self.control_meeting.conversation, user=self.host
        )
        self.control_occurrence_start = current_occurrence(
            self.control_meeting, now=self.now
        )[0]
        self.control_guest, self.control_token = guest_with_token(
            self.control_meeting,
            self.control_occurrence_start,
            display_name=SENTINEL_GUEST_NAME,
        )

        self.sentinels = [
            SENTINEL_CONVERSATION_TITLE,
            SENTINEL_MEETING_TITLE,
            SENTINEL_CALENDAR_NAME,
            SENTINEL_MESSAGE_BODY,
            SENTINEL_GUEST_NAME,
            FOREIGN_USERNAME,
            "Zoe Sentinel-Foreign",
            self.control_meeting.slug,
            self.control_token,
            str(self.control_meeting.uuid),
            str(self.control_meeting.conversation_id),
            str(self.control_conversation.uuid),
            str(self.control_event.uuid),
            str(self.control_guest.uuid),
            str(self.control_message.uuid),
        ]

    # --- the audit itself ---

    def assert_contained(self, label, text):
        """Fail if *text* carries anything from outside the guest's meeting.

        Case-insensitive: a uuid re-spelled in upper case is the same
        disclosure. Emails and foreign conversation ids are re-read from the
        database on every call rather than snapshotted in setUp, so a row a
        test creates on its own is covered too.
        """
        haystack = text.lower()

        for sentinel in self.sentinels:
            self.assertNotIn(
                sentinel.lower(), haystack, f"{label} leaked sentinel {sentinel!r}"
            )

        for email in User.objects.exclude(email="").values_list("email", flat=True):
            self.assertNotIn(
                email.lower(), haystack, f"{label} leaked the address {email!r}"
            )

        foreign_conversation_ids = Conversation.objects.exclude(
            uuid=self.meeting.conversation_id
        ).values_list("uuid", flat=True)
        for conversation_id in foreign_conversation_ids:
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
        calls.start_or_join_call(self.host, self.meeting.conversation_id)
        media_state = {"audio": True, "video": False, "screen": False}
        header = {"HTTP_X_MEETING_TOKEN": self.token}

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
                    {"body": "hello from the guest"},
                    format="json",
                    **header,
                ),
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
            ),
        ]

        for label, expected_status, response in walk:
            with self.subTest(route=label):
                self.assertEqual(response.status_code, expected_status, label)
                self.assert_contained(label, self.body_text(response))

        stream_response, frames = self.drain_stream(self.token)
        self.assertEqual(stream_response.status_code, 200)
        self.assert_contained("GET stream", "".join(frames))

        leave = self.client.post(self.url("leave"), **header)
        self.assertEqual(leave.status_code, 200)
        self.assert_contained("POST leave", self.body_text(leave))

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
        calls.start_or_join_call(self.host, self.meeting.conversation_id)
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

        self.assertFalse(Message.objects.filter(guest=self.control_guest).exists())


class GuestStreamContainmentTests(GuestContainmentFixture):
    def test_the_admitted_guests_stream_carries_nothing_foreign(self):
        """Every SSE frame an admitted guest receives, held to the same bar as
        a REST body - the stream is the only guest surface that pushes without
        being asked."""
        calls.start_or_join_call(self.host, self.meeting.conversation_id)
        calls.join_call_as_guest(self.guest)
        # A second member joining is a real fan-out that reaches the guest's
        # mailbox, rather than an event enqueued by hand.
        calls.start_or_join_call(self.member, self.meeting.conversation_id)
        Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.host,
            body="a message during the call",
        )

        clock = FakeClock(self.now, max_cycles=1)
        frames, _terminated = drive_guest_stream(self.token, self.meeting.uuid, clock)
        names = [parse_sse(f)[0] for f in frames if not f.startswith(":")]

        self.assertIn("message", names)
        self.assertTrue(any(name.startswith("call_") for name in names), names)
        self.assert_contained("guest stream", "".join(frames))

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
        calls.start_or_join_call(self.host, self.meeting.conversation_id)
        Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.host,
            body="members only, for now",
        )
        key = guest_key(waiting_guest.uuid)
        # meeting_admitted is the positive control: without it the test would
        # also pass if nothing at all were being drained.
        enqueue_event(key, "meeting_admitted", {"meeting_id": str(self.meeting.uuid)})
        enqueue_event(
            key,
            "call_started",
            {
                "session_id": "x",
                "conversation_id": str(self.meeting.conversation_id),
            },
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
        calls.start_or_join_call(self.host, self.meeting.conversation_id)
        Message.objects.create(
            conversation=self.meeting.conversation,
            author=self.host,
            body="posted while the guest waited",
        )
        key = guest_key(waiting_guest.uuid)
        enqueue_event(key, "call_started", {"session_id": "x"})

        def _admit_and_fan_out():
            admit_guest(waiting_guest, self.host)
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
        self.assertIn("message", names)
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


def _has_empty_authentication_classes(class_node):
    for statement in class_node.body:
        if not isinstance(statement, ast.Assign):
            continue
        if not isinstance(statement.value, ast.List) or statement.value.elts:
            continue
        for target in statement.targets:
            if isinstance(target, ast.Name) and target.id == "authentication_classes":
                return True
    return False


class GuestSurfaceSourceTests(SimpleTestCase):
    """The structural invariants the runtime rules rest on, read off the
    source so a change to any of them fails here rather than in production."""

    # Every view that answers without authenticating anyone. Emptying
    # authentication_classes is what makes a route reachable by a stranger, so
    # the set is enumerated rather than counted: an eleventh anonymous view is
    # a decision, not an accident.
    ANONYMOUS_VIEWS = {
        "avatar.py": {"GroupAvatarRetrieveView"},
        "meetings.py": {"MeetingSummaryView", "MeetingKnockView"},
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
        self.assertEqual(sum(len(names) for names in found.values()), 10)

    def test_no_service_imports_a_view(self):
        """Services stay callable from a view, a task or a command alike; a
        service reaching back into ``views`` would drag a request-shaped,
        permission-checked layer into paths that have neither."""
        offenders = []
        for path in sorted(SERVICES_DIR.rglob("*.py")):
            for node in ast.walk(_parse_module(path)):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    absolute = node.level == 0 and module.startswith(
                        "workspace.chat.views"
                    )
                    relative = node.level >= 1 and (
                        module == "views" or module.startswith("views.")
                    )
                    if absolute or relative:
                        offenders.append(f"{path.name}:{node.lineno}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith("workspace.chat.views"):
                            offenders.append(f"{path.name}:{node.lineno}")

        self.assertEqual(offenders, [])

    def test_no_public_view_calls_get_active_call(self):
        """``get_active_call`` self-heals: it takes ``select_for_update``, can
        end a stale session and broadcasts. An anonymous caller must never
        drive that, so the public paths read the call through
        ``is_call_locked`` / ``active_call_session_for_guest`` instead."""
        guest_views = _parse_module(VIEWS_DIR / "meeting_guest.py")
        self.assertEqual(_calls_to(guest_views, "get_active_call"), [])

        meetings = _parse_module(VIEWS_DIR / "meetings.py")
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

    def test_guest_paths_serialize_messages_only_for_a_guest_audience(self):
        """``MessageSerializer`` emits ``conversation_id`` and hydrates
        ``reply_to``/``thread_root`` below the occurrence floor.
        ``GuestMessageSerializer`` is the redaction, and it is only a fence
        while it is the single one these three files use."""
        used = set()
        for path in (
            VIEWS_DIR / "meetings.py",
            VIEWS_DIR / "meeting_guest.py",
            SERVICES_DIR / "guest_stream.py",
        ):
            for node in ast.walk(_parse_module(path)):
                if not isinstance(node, ast.Call):
                    continue
                name = _called_name(node.func)
                if name and name.endswith("MessageSerializer"):
                    used.add(name)

        self.assertEqual(used, {"GuestMessageSerializer"})
