from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from workspace.calendar.models import Calendar, Event
from workspace.chat.models import (
    Conversation,
    ConversationMember,
    Meeting,
    MeetingGuest,
    Message,
)
from workspace.chat.search import search_chat_messages

User = get_user_model()


class SearchChatMessagesGuestTests(TestCase):
    """Pins the unified-search crash site: a guest-authored message must be
    named by the guest's display name rather than raising on a None author."""

    def setUp(self):
        self.user = User.objects.create_user(username="searcher", password="pw")
        self.conv = Conversation.objects.create(
            kind=Conversation.Kind.GROUP, created_by=self.user
        )
        ConversationMember.objects.create(conversation=self.conv, user=self.user)
        cal = Calendar.objects.create(name="C", owner=self.user)
        event = Event.objects.create(
            calendar=cal, owner=self.user, title="E", start=timezone.now()
        )
        meeting = Meeting.objects.create(
            event=event, conversation=self.conv, created_by=self.user
        )
        self.guest = MeetingGuest.objects.create(
            meeting=meeting,
            display_name="Visitor",
            occurrence_start=timezone.now(),
            token_hash="c" * 64,
        )

    def test_guest_authored_message_is_named_by_display_name(self):
        Message.objects.create(
            conversation=self.conv, guest=self.guest, body="keyword hit"
        )

        results = search_chat_messages("keyword", self.user, limit=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "Visitor")
