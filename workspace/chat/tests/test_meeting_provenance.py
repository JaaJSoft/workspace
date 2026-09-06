"""What a conversation payload says about the meeting it was created for.

A meeting's conversation looks like any other group from the inside: same
title, same members, nothing tying it back to the event it exists for. The
``meeting`` field is that tie, and it comes in two shapes on purpose - see
``_meeting_payload`` in ``chat/serializers.py``. This module pins both shapes,
the query cost of the cheap one, and the fact that a guest is served none of
it.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from workspace.chat.models import Conversation, ConversationMember
from workspace.chat.serializers import ConversationListSerializer
from workspace.chat.services.meetings import create_meeting, set_locked
from workspace.chat.tests.meeting_fixtures import make_event

User = get_user_model()


class MeetingProvenanceFieldTests(TestCase):
    def setUp(self):
        self.host = User.objects.create_user("prov-host", "prov@example.com", "pw")
        now = timezone.now()
        self.event = make_event(
            self.host,
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=25),
            title="Weekly standup",
        )
        self.meeting = create_meeting(self.event, self.host)
        self.plain = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, title="Plain", created_by=self.host
        )
        ConversationMember.objects.create(conversation=self.plain, user=self.host)
        request = RequestFactory().get("/chat")
        request.user = self.host
        self.context = {"request": request}

    def _data(self, conversation, **context):
        return ConversationListSerializer(
            conversation, context={**self.context, **context}
        ).data

    def test_a_conversation_that_is_not_a_meetings_carries_none(self):
        self.assertIsNone(self._data(self.plain)["meeting"])

    def test_the_list_shape_carries_the_event_title_and_the_join_link(self):
        payload = self._data(self.meeting.conversation)["meeting"]
        self.assertEqual(
            set(payload),
            {"event_title", "join_url"},
            "the list shape must not compute the occurrence",
        )
        self.assertEqual(payload["event_title"], "Weekly standup")
        self.assertTrue(payload["join_url"].endswith(f"/meet/{self.meeting.slug}"))

    def test_the_single_conversation_shape_adds_the_occurrence_and_the_lock(self):
        payload = self._data(
            self.meeting.conversation, include_meeting_occurrence=True
        )["meeting"]
        self.assertEqual(
            set(payload), {"event_title", "join_url", "next_start", "locked"}
        )
        self.assertEqual(
            payload["next_start"],
            self.event.start.replace(microsecond=0).isoformat(),
        )
        self.assertFalse(payload["locked"])

    def test_next_start_is_null_when_no_occurrence_is_reachable(self):
        self.event.start = timezone.now() + timedelta(days=3)
        self.event.end = self.event.start + timedelta(minutes=30)
        self.event.save(update_fields=["start", "end"])
        payload = self._data(
            self.meeting.conversation, include_meeting_occurrence=True
        )["meeting"]
        self.assertIsNone(payload["next_start"])

    def test_the_lock_the_host_set_is_reported(self):
        set_locked(self.meeting, True)
        payload = self._data(
            self.meeting.conversation, include_meeting_occurrence=True
        )["meeting"]
        self.assertTrue(payload["locked"])


class MeetingProvenanceQueryCountTests(TestCase):
    """The list shape is only cheap if the meeting and its event ride along
    with the conversation row. Reading them per conversation is invisible on
    a fixture with one meeting, so this measures a list of one against a list
    of five and demands the same number of queries."""

    def _user_with_meetings(self, username, count):
        user = User.objects.create_user(username, f"{username}@example.com", "pw")
        now = timezone.now()
        for i in range(count):
            event = make_event(
                user,
                start=now - timedelta(minutes=5),
                end=now + timedelta(minutes=25),
                title=f"Event {i}",
            )
            create_meeting(event, user)
        return user

    def _queries(self, user, url):
        self.client.force_login(user)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries)

    def test_the_chat_page_costs_the_same_for_one_meeting_and_for_five(self):
        url = reverse("chat_ui:index")
        one = self._queries(self._user_with_meetings("one-chat", 1), url)
        five = self._queries(self._user_with_meetings("five-chat", 5), url)
        self.assertEqual(five, one, f"{five} queries for five meetings, {one} for one")

    def test_the_conversation_api_costs_the_same_for_one_meeting_and_for_five(self):
        url = "/api/v1/chat/conversations"
        one = self._queries(self._user_with_meetings("one-api", 1), url)
        five = self._queries(self._user_with_meetings("five-api", 5), url)
        self.assertEqual(five, one, f"{five} queries for five meetings, {one} for one")


class MeetingProvenanceGuestTests(TestCase):
    """A guest is told the meeting's own title by the summary endpoint and
    nothing else. The provenance field lives on the member serializers, so
    the public page must carry no conversation payload at all."""

    def setUp(self):
        self.host = User.objects.create_user("guest-host", "gh@example.com", "pw")
        now = timezone.now()
        self.event = make_event(
            self.host,
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=25),
            title="Weekly standup",
        )
        self.meeting = create_meeting(self.event, self.host)

    def test_the_public_meeting_page_embeds_no_conversation_payload(self):
        html = self.client.get(f"/meet/{self.meeting.slug}").content.decode()
        self.assertNotIn('"meeting"', html)
        self.assertNotIn("event_title", html)
        self.assertNotIn("join_url", html)
        self.assertNotIn("room-conversation-data", html)


class MeetingProvenanceRefetchQueryTests(TestCase):
    """The two write paths that answer with a whole conversation refetch it
    with their own queryset, so each one needs the join of its own.

    Measured by what a standalone ``FROM "chat_meeting"`` select means: with
    the join in place the meeting rides in the conversation's own SELECT and
    no such query is issued at all, while without it ``_meeting_payload``
    goes looking for the row itself. Counting queries mentioning the table
    would not tell the two apart - the join mentions it too.
    """

    def setUp(self):
        self.owner = User.objects.create_user("refetch", "refetch@example.com", "pw")
        self.other = User.objects.create_user("refetch2", "refetch2@example.com", "pw")
        self.client.force_login(self.owner)

    def _standalone_meeting_queries(self, captured):
        return [
            q["sql"]
            for q in captured
            if 'FROM "chat_meeting"' in q["sql"] or 'FROM "calendar_event"' in q["sql"]
        ]

    def test_creating_a_conversation_looks_for_no_meeting_of_its_own(self):
        """The group branch answers from a refetch, which is the queryset
        that needs the join; a brand-new conversation has no meeting, and
        finding that out must not cost a query of its own."""
        group = Group.objects.create(name="Refetch team")
        self.owner.groups.add(group)
        self.other.groups.add(group)

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.post(
                "/api/v1/chat/conversations",
                {"group_ids": [group.pk]},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.json()["meeting"])
        self.assertEqual(self._standalone_meeting_queries(ctx.captured_queries), [])

    def test_adding_a_member_to_a_meeting_looks_for_no_meeting_of_its_own(self):
        now = timezone.now()
        event = make_event(
            self.owner,
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=25),
            title="Weekly standup",
        )
        meeting = create_meeting(event, self.owner)

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.post(
                f"/api/v1/chat/conversations/{meeting.conversation_id}/members",
                {"user_ids": [self.other.id]},
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["meeting"]["event_title"],
            "Weekly standup",
            "the refetched payload still carries the provenance",
        )
        self.assertEqual(self._standalone_meeting_queries(ctx.captured_queries), [])
