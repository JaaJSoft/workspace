from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.notifications.models import Notification
from workspace.notifications.services.notifications import get_unread_badges, notify

User = get_user_model()


class UnreadBadgesTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="badgeuser", password="pass")
        self.other = User.objects.create_user(username="badgeother", password="pass")

    def tearDown(self):
        cache.clear()

    def _notif(self, origin="chat", url="", read=False, recipient=None):
        return Notification.objects.create(
            recipient=recipient or self.user,
            origin=origin,
            icon="i",
            title="t",
            url=url,
            read_at=timezone.now() if read else None,
        )

    def test_counts_unread_per_origin(self):
        self._notif(origin="chat")
        self._notif(origin="chat")
        self._notif(origin="mail")

        badges = get_unread_badges(self.user)

        self.assertEqual(badges["chat"]["count"], 2)
        self.assertEqual(badges["mail"]["count"], 1)

    def test_read_notifications_do_not_badge(self):
        self._notif(origin="chat", read=True)

        self.assertEqual(get_unread_badges(self.user), {})

    def test_other_users_notifications_do_not_badge(self):
        self._notif(origin="chat", recipient=self.other)

        self.assertEqual(get_unread_badges(self.user), {})

    def test_single_unread_carries_its_url(self):
        self._notif(origin="chat", url="/chat/abc")

        self.assertEqual(get_unread_badges(self.user)["chat"]["url"], "/chat/abc")

    def test_single_unread_without_url_has_no_target(self):
        self._notif(origin="chat", url="")

        self.assertIsNone(get_unread_badges(self.user)["chat"]["url"])

    def test_several_unread_have_no_target(self):
        self._notif(origin="chat", url="/chat/abc")
        self._notif(origin="chat", url="/chat/def")

        badge = get_unread_badges(self.user)["chat"]
        self.assertEqual(badge["count"], 2)
        self.assertIsNone(badge["url"])

    @patch("workspace.notifications.services.notifications.send_push_notification")
    def test_cache_refreshes_when_a_notification_arrives(self, mock_push):
        self.assertEqual(get_unread_badges(self.user), {})

        notify(recipient=self.user, origin="chat", title="hi")

        self.assertEqual(get_unread_badges(self.user)["chat"]["count"], 1)
