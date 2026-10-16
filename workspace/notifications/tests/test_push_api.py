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
            username="testuser",
            email="test@test.com",
            password="pass123",
        )
        self.client.force_authenticate(user=self.user)

    def test_returns_vapid_public_key(self):
        with self.settings(WEBPUSH_VAPID_PUBLIC_KEY="test-public-key-123"):
            response = self.client.get("/api/v1/notifications/push/key")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["public_key"], "test-public-key-123")

    def test_returns_empty_when_not_configured(self):
        with self.settings(WEBPUSH_VAPID_PUBLIC_KEY=""):
            response = self.client.get("/api/v1/notifications/push/key")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["public_key"], "")


class PushSubscribeViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="testuser",
            email="test@test.com",
            password="pass123",
        )
        self.client.force_authenticate(user=self.user)

    def test_subscribe_creates_subscription(self):
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/abc123",
                "keys": {
                    "p256dh": "test-p256dh-key",
                    "auth": "test-auth-key",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            PushSubscription.objects.filter(
                user=self.user,
                endpoint="https://push.example.com/sub/abc123",
            ).exists()
        )

    def test_subscribe_updates_existing_endpoint(self):
        PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example.com/sub/abc123",
            p256dh="old-p256dh",
            auth="old-auth",
        )
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/abc123",
                "keys": {
                    "p256dh": "new-p256dh",
                    "auth": "new-auth",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        sub = PushSubscription.objects.get(
            endpoint="https://push.example.com/sub/abc123"
        )
        self.assertEqual(sub.p256dh, "new-p256dh")
        self.assertEqual(sub.auth, "new-auth")
        self.assertEqual(
            PushSubscription.objects.filter(
                endpoint="https://push.example.com/sub/abc123",
            ).count(),
            1,
        )

    def test_subscribe_refuses_another_users_endpoint_without_its_keys(self):
        other = User.objects.create_user(username="other", password="pass123")
        PushSubscription.objects.create(
            user=other,
            endpoint="https://push.example.com/sub/abc123",
            p256dh="their-p256dh",
            auth="their-auth",
        )
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/abc123",
                "keys": {"p256dh": "attacker-p256dh", "auth": "attacker-auth"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertNotIn("other", str(response.data))
        sub = PushSubscription.objects.get(
            endpoint="https://push.example.com/sub/abc123"
        )
        self.assertEqual(sub.user, other)
        self.assertEqual(sub.p256dh, "their-p256dh")
        self.assertEqual(sub.auth, "their-auth")

    def test_subscribe_with_the_same_keys_moves_the_endpoint_to_the_caller(self):
        # A shared browser re-subscribing under a new login presents the
        # subscription it already holds, keys included.
        other = User.objects.create_user(username="other", password="pass123")
        PushSubscription.objects.create(
            user=other,
            endpoint="https://push.example.com/sub/abc123",
            p256dh="browser-p256dh",
            auth="browser-auth",
        )
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/abc123",
                "keys": {"p256dh": "browser-p256dh", "auth": "browser-auth"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        sub = PushSubscription.objects.get(
            endpoint="https://push.example.com/sub/abc123"
        )
        self.assertEqual(sub.user, self.user)

    def test_subscribe_with_only_one_matching_key_is_refused(self):
        other = User.objects.create_user(username="other", password="pass123")
        PushSubscription.objects.create(
            user=other,
            endpoint="https://push.example.com/sub/abc123",
            p256dh="browser-p256dh",
            auth="browser-auth",
        )
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/abc123",
                "keys": {"p256dh": "browser-p256dh", "auth": "guessed-auth"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            PushSubscription.objects.get(
                endpoint="https://push.example.com/sub/abc123"
            ).user,
            other,
        )

    def test_subscribe_with_non_string_keys_returns_400(self):
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/abc123",
                "keys": {"p256dh": 12, "auth": ["a"]},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_subscribe_with_non_ascii_keys_on_a_foreign_endpoint_is_refused(self):
        other = User.objects.create_user(username="other", password="pass123")
        PushSubscription.objects.create(
            user=other,
            endpoint="https://push.example.com/sub/abc123",
            p256dh="their-p256dh",
            auth="their-auth",
        )
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/abc123",
                "keys": {"p256dh": "clé", "auth": "clé"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_subscribe_missing_fields_returns_400(self):
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/abc123",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unsubscribe_deletes_subscription(self):
        PushSubscription.objects.create(
            user=self.user,
            endpoint="https://push.example.com/sub/abc123",
            p256dh="test-p256dh",
            auth="test-auth",
        )
        response = self.client.delete(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/abc123",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            PushSubscription.objects.filter(
                endpoint="https://push.example.com/sub/abc123",
            ).exists()
        )

    def test_unsubscribe_nonexistent_returns_204(self):
        response = self.client.delete(
            "/api/v1/notifications/push/subscribe",
            {
                "endpoint": "https://push.example.com/sub/nonexistent",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
