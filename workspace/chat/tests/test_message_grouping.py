from datetime import UTC, date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone as dj_timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Meeting,
    MeetingGuest,
    Message,
)
from workspace.chat.ui.views import group_messages

User = get_user_model()


class GroupMessagesTimezoneTests(TestCase):
    def tearDown(self):
        dj_timezone.deactivate()

    def test_date_dividers_split_on_user_local_days(self):
        user = User.objects.create_user(username="grp", password="p")
        # Same UTC day, but 23:30 UTC is already the next day in Paris.
        m1 = Message(
            author=user,
            kind=Message.Kind.USER,
            body="a",
            created_at=datetime(2026, 1, 31, 22, 0, tzinfo=UTC),
        )
        m2 = Message(
            author=user,
            kind=Message.Kind.USER,
            body="b",
            created_at=datetime(2026, 1, 31, 23, 30, tzinfo=UTC),
        )
        dj_timezone.activate("Europe/Paris")
        groups = group_messages([m1, m2], user)
        dates = [g["date"] for g in groups if g["type"] == "date"]
        self.assertEqual(dates, [date(2026, 1, 31), date(2026, 2, 1)])


class MessageGroupRenderGuestTests(TestCase):
    """A guest-authored message must render on the real chat page, not just
    survive a call to group_messages in isolation - the crash this pins down
    happened past that function, inside the grouping comparison itself."""

    def setUp(self):
        self.user = User.objects.create_user(username="grouphost", password="p")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        cal = Calendar.objects.create(name="C", owner=self.user)
        event = Event.objects.create(
            calendar=cal, owner=self.user, title="E", start=dj_timezone.now()
        )
        meeting = Meeting.objects.create(
            event=event, conversation=self.conv, created_by=self.user
        )
        self.guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name="Visitor",
            occurrence_start=dj_timezone.now(),
            token_hash="a" * 64,
        )

    def test_guest_authored_message_renders_on_the_chat_page(self):
        Message.objects.create(
            conversation=self.conv, guest=self.guest, body="hi from a guest"
        )

        self.client.force_login(self.user)
        resp = self.client.get(f"/chat/{self.conv.uuid}/messages")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "hi from a guest")
        self.assertContains(resp, 'author-name="Visitor"')

    def test_two_consecutive_guest_messages_group_together(self):
        Message.objects.create(conversation=self.conv, guest=self.guest, body="first")
        Message.objects.create(conversation=self.conv, guest=self.guest, body="second")

        self.client.force_login(self.user)
        resp = self.client.get(f"/chat/{self.conv.uuid}/messages")

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "first")
        self.assertContains(resp, "second")
        self.assertEqual(resp.content.count(b'author-name="Visitor"'), 1)
