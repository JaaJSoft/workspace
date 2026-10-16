from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.notifications.models import PushSubscription
from workspace.notifications.services.push_subscriptions import (
    save_push_subscription,
)

User = get_user_model()

ENDPOINT = "https://push.example.com/sub/abc123"


class SavePushSubscriptionTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")

    def test_new_endpoint_is_created_for_the_caller(self):
        self.assertTrue(save_push_subscription(self.alice, ENDPOINT, "p", "a"))
        self.assertEqual(
            PushSubscription.objects.get(endpoint=ENDPOINT).user, self.alice
        )

    def test_owner_may_rotate_the_keys(self):
        PushSubscription.objects.create(
            user=self.alice, endpoint=ENDPOINT, p256dh="old-p", auth="old-a"
        )
        self.assertTrue(save_push_subscription(self.alice, ENDPOINT, "new-p", "new-a"))
        sub = PushSubscription.objects.get(endpoint=ENDPOINT)
        self.assertEqual((sub.p256dh, sub.auth), ("new-p", "new-a"))

    def test_foreign_endpoint_needs_both_stored_keys(self):
        PushSubscription.objects.create(
            user=self.bob, endpoint=ENDPOINT, p256dh="p", auth="a"
        )
        self.assertFalse(save_push_subscription(self.alice, ENDPOINT, "p", "x"))
        self.assertFalse(save_push_subscription(self.alice, ENDPOINT, "x", "a"))
        self.assertEqual(PushSubscription.objects.get(endpoint=ENDPOINT).user, self.bob)

        self.assertTrue(save_push_subscription(self.alice, ENDPOINT, "p", "a"))
        self.assertEqual(
            PushSubscription.objects.get(endpoint=ENDPOINT).user, self.alice
        )
