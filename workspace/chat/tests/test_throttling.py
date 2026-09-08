"""MeetingPublicIpThrottle, pinned the way workspace/vault/tests/test_throttling.py
pins the vault's equivalent: nothing else in the suite drives 31 requests
against the same endpoint, so without this the throttle_classes entry could
be deleted from either public view and nothing would notice.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.calendar.models import Calendar, Event
from workspace.chat.services.meetings import create_meeting

User = get_user_model()


def make_event(owner, start=None, end=None):
    cal = Calendar.objects.create(name="Cal", owner=owner)
    return Event.objects.create(
        calendar=cal,
        owner=owner,
        title="Standup",
        start=start or timezone.now(),
        end=end,
    )


class MeetingPublicIpThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = User.objects.create_user(username="throttle-host", password="x")
        now = timezone.now()
        self.event = make_event(
            self.owner,
            start=now - timedelta(minutes=5),
            end=now + timedelta(minutes=25),
        )
        self.meeting = create_meeting(self.event, self.owner)
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    def test_31st_summary_request_in_a_minute_is_throttled_per_ip(self):
        url = f"/api/v1/chat/meet/{self.meeting.slug}"
        for _ in range(30):
            resp = self.client.get(url, REMOTE_ADDR="198.51.100.1")
            self.assertEqual(resp.status_code, 200)

        blocked = self.client.get(url, REMOTE_ADDR="198.51.100.1")
        self.assertEqual(blocked.status_code, 429)

        # A different IP has spent none of that budget - the scope is
        # per-caller, not a single shared bucket for the whole endpoint.
        other = self.client.get(url, REMOTE_ADDR="198.51.100.2")
        self.assertEqual(other.status_code, 200)
