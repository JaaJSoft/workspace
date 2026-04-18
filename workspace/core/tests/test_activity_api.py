from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

User = get_user_model()


class ActivityRecentViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@test.com', password='pass123',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_recent_requires_auth(self):
        client = APIClient()
        resp = client.get('/api/v1/activity/recents')
        self.assertIn(resp.status_code, (401, 403))

    def test_recent_returns_200(self):
        resp = self.client.get('/api/v1/activity/recents')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('events', resp.json())
        self.assertIn('sources', resp.json())


class ActivityDailyCountsViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@test.com', password='pass123',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_daily_counts_requires_date_params(self):
        resp = self.client.get('/api/v1/activity/daily-counts')
        self.assertEqual(resp.status_code, 400)

    def test_daily_counts_returns_200(self):
        resp = self.client.get('/api/v1/activity/daily-counts', {
            'date_from': '2026-03-01',
            'date_to': '2026-03-31',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertIn('counts', resp.json())


class ActivityStatsViewTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@test.com', password='pass123',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_stats_requires_auth(self):
        client = APIClient()
        resp = client.get('/api/v1/activity/stats')
        self.assertIn(resp.status_code, (401, 403))

    def test_stats_returns_200(self):
        resp = self.client.get('/api/v1/activity/stats')
        self.assertEqual(resp.status_code, 200)
