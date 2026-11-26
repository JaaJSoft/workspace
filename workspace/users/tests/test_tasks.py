from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.users.models import UserPresence
from workspace.users.tasks import sync_presence

User = get_user_model()


class SyncPresenceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")

    def tearDown(self):
        cache.clear()

    def test_creates_the_row_at_the_recorded_time(self):
        seen_at = timezone.now() - timedelta(seconds=5)

        sync_presence(self.user.pk, seen_at.isoformat())

        row = UserPresence.objects.get(user=self.user)
        self.assertEqual(row.last_seen, seen_at)

    def test_moves_last_seen_of_an_existing_row(self):
        UserPresence.objects.create(
            user=self.user, last_seen=timezone.now() - timedelta(hours=1)
        )
        seen_at = timezone.now()

        sync_presence(self.user.pk, seen_at.isoformat())

        self.assertEqual(UserPresence.objects.get(user=self.user).last_seen, seen_at)
