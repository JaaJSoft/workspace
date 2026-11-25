from datetime import UTC, date, datetime

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


class GroupContinuationTests(TestCase):
    def _msg(self, author, hour, minute=0):
        return Message(
            author=author,
            kind=Message.Kind.USER,
            body="x",
            created_at=datetime(2026, 1, 15, hour, minute, tzinfo=UTC),
        )

    def test_a_group_split_only_by_the_time_window_continues_the_one_above(self):
        alice = User.objects.create_user(username="alice", password="p")
        bob = User.objects.create_user(username="bob", password="p")
        groups = group_messages(
            [
                self._msg(alice, 9),
                self._msg(alice, 10),  # past the 5-minute window
                self._msg(bob, 10, 1),
                self._msg(alice, 10, 2),
            ],
            bob,
        )
        flags = [g["continues"] for g in groups if g["type"] == "messages"]
        self.assertEqual(flags, [False, True, False, False])

    def test_a_new_day_does_not_continue_the_day_before(self):
        alice = User.objects.create_user(username="alice", password="p")
        first = self._msg(alice, 9)
        next_day = Message(
            author=alice,
            kind=Message.Kind.USER,
            body="x",
            created_at=datetime(2026, 1, 16, 9, tzinfo=UTC),
        )
        groups = group_messages([first, next_day], alice)
        flags = [g["continues"] for g in groups if g["type"] == "messages"]
        self.assertEqual(flags, [False, False])
