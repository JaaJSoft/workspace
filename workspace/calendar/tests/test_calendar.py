from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from workspace.calendar.models import Calendar, Event, EventMember

User = get_user_model()


class CalendarTestMixin:
    """Common setup for calendar tests.

    Kept here so other test files in this package can do
    ``from .test_calendar import CalendarTestMixin``.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="owner@test.com",
            password="pass123",
        )
        self.member = User.objects.create_user(
            username="member",
            email="member@test.com",
            password="pass123",
        )
        self.outsider = User.objects.create_user(
            username="outsider",
            email="outsider@test.com",
            password="pass123",
        )

        self.calendar = Calendar.objects.create(
            name="Work",
            owner=self.owner,
        )

        self.event = Event.objects.create(
            calendar=self.calendar,
            title="Team Meeting",
            start=timezone.now() + timedelta(days=1),
            end=timezone.now() + timedelta(days=1, hours=1),
            owner=self.owner,
        )
        EventMember.objects.create(event=self.event, user=self.member)
