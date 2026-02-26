from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase

from workspace.notifications.models import PushSubscription

User = get_user_model()


class PushSubscriptionModelTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser', email='test@test.com', password='pass123',
        )

    def test_create_subscription(self):
        sub = PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.com/sub/abc123',
            p256dh='test-p256dh-key',
            auth='test-auth-key',
        )
        self.assertIsNotNone(sub.uuid)
        self.assertEqual(sub.user, self.user)
        self.assertEqual(sub.endpoint, 'https://push.example.com/sub/abc123')
        self.assertEqual(sub.p256dh, 'test-p256dh-key')
        self.assertEqual(sub.auth, 'test-auth-key')
        self.assertIsNotNone(sub.created_at)

    def test_endpoint_unique(self):
        PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.com/sub/unique',
            p256dh='key1',
            auth='auth1',
        )
        other_user = User.objects.create_user(
            username='other', email='other@test.com', password='pass123',
        )
        with self.assertRaises(IntegrityError):
            PushSubscription.objects.create(
                user=other_user,
                endpoint='https://push.example.com/sub/unique',
                p256dh='key2',
                auth='auth2',
            )

    def test_str(self):
        sub = PushSubscription.objects.create(
            user=self.user,
            endpoint='https://push.example.com/subscription/very-long-endpoint-identifier',
            p256dh='key',
            auth='auth',
        )
        result = str(sub)
        self.assertIn('testuser', result)
        self.assertTrue(result.startswith('PushSubscription('))
        self.assertTrue(result.endswith('...)'))
