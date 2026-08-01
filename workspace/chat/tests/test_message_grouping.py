from datetime import date, datetime, timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone as dj_timezone

from workspace.chat.models import Message
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
            created_at=datetime(2026, 1, 31, 22, 0, tzinfo=dt_timezone.utc),
        )
        m2 = Message(
            author=user,
            kind=Message.Kind.USER,
            body="b",
            created_at=datetime(2026, 1, 31, 23, 30, tzinfo=dt_timezone.utc),
        )
        dj_timezone.activate("Europe/Paris")
        groups = group_messages([m1, m2], user)
        dates = [g["date"] for g in groups if g["type"] == "date"]
        self.assertEqual(dates, [date(2026, 1, 31), date(2026, 2, 1)])
