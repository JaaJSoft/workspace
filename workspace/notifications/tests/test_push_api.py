from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from workspace.notifications.models import PushSubscription

User = get_user_model()


class PushVapidKeyViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser', email='test@test.com', password='pass123',
        )
        self.client.force_authenticate(user=self.user)

    def test_returns_vapid_public_key(self):
        with self.settings(WEBPUSH_VAPID_PUBLIC_KEY='test-public-key-123'):
            response = self.client.get('/api/v1/notifications/push/key')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['public_key'], 'test-public-key-123')

    def test_returns_empty_when_not_configured(self):
        with self.settings(WEBPUSH_VAPID_PUBLIC_KEY=''):
            response = self.client.get('/api/v1/notifications/push/key')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['public_key'], '')


class PushSubscribeViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username='testuser', email='test@test.com', password='pass123',
        )
        self.client.force_authenticate(user=self.user)

    def test_subscribe_creates_subscription(self):
        response = self.client.post('/api/v1/notifications/push/subscribe', {
            'endpoint': 'https://push.example.com/sub/abc123',
            'keys': {
                'p256dh': 'test-p256dh-key',
                'auth': 'test-auth-key',
            },
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(PushSubscription.objects.filter(
            user=self.user,
            endpoint='https://push.example.com/sub/abc123',
        ).exists())

    def test_subscribe_updates_existing_endpoint(self):
        PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.com/sub/abc123',
            p256dh='old-p256dh',
            auth='old-auth',
        )
        response = self.client.post('/api/v1/notifications/push/subscribe', {
            'endpoint': 'https://push.example.com/sub/abc123',
            'keys': {
                'p256dh': 'new-p256dh',
                'auth': 'new-auth',
            },
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        sub = PushSubscription.objects.get(endpoint='https://push.example.com/sub/abc123')
        self.assertEqual(sub.p256dh, 'new-p256dh')
        self.assertEqual(sub.auth, 'new-auth')
        self.assertEqual(PushSubscription.objects.filter(
            endpoint='https://push.example.com/sub/abc123',
        ).count(), 1)

    def test_subscribe_missing_fields_returns_400(self):
        response = self.client.post('/api/v1/notifications/push/subscribe', {
            'endpoint': 'https://push.example.com/sub/abc123',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unsubscribe_deletes_subscription(self):
        PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.com/sub/abc123',
            p256dh='test-p256dh',
            auth='test-auth',
        )
        response = self.client.delete('/api/v1/notifications/push/subscribe', {
            'endpoint': 'https://push.example.com/sub/abc123',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(PushSubscription.objects.filter(
            endpoint='https://push.example.com/sub/abc123',
        ).exists())

    def test_unsubscribe_nonexistent_returns_204(self):
        response = self.client.delete('/api/v1/notifications/push/subscribe', {
            'endpoint': 'https://push.example.com/sub/nonexistent',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
