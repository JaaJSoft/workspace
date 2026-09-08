from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from workspace.chat.models import Meeting, MeetingGuest, MeetingMessage


class MeetingMessageModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="mm", password="x")
        self.meeting = Meeting.objects.create(title="Ad hoc", created_by=self.user)
        self.guest = MeetingGuest.objects.create(
            meeting=self.meeting,
            display_name="Ada",
            state=MeetingGuest.State.ADMITTED,
            occurrence_start=timezone.now(),
            token_hash="a" * 64,
        )

    def test_exactly_one_identity(self):
        MeetingMessage.objects.create(meeting=self.meeting, author=self.user, body="hi")
        MeetingMessage.objects.create(meeting=self.meeting, guest=self.guest, body="hi")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MeetingMessage.objects.create(meeting=self.meeting, body="nobody")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                MeetingMessage.objects.create(
                    meeting=self.meeting,
                    author=self.user,
                    guest=self.guest,
                    body="both",
                )

    def test_a_bound_guest_keeps_its_row_when_the_user_goes(self):
        # A different account than the meeting's creator, so deleting it
        # cannot cascade through Meeting.created_by and take the guest with it.
        visitor = get_user_model().objects.create_user(username="visitor", password="x")
        self.guest.user = visitor
        self.guest.save(update_fields=["user"])
        visitor.delete()
        self.guest.refresh_from_db()
        self.assertIsNone(self.guest.user_id)
