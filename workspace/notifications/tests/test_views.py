from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.common.pagination import OptInLimitOffsetPagination
from workspace.notifications.models import Notification

User = get_user_model()


class NotifViewMixin:
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.client.force_authenticate(self.alice)

    def _make_notif(self, user=None, **kwargs):
        defaults = {
            "recipient": user or self.alice,
            "origin": "chat",
            "icon": "msg",
            "title": "Test",
        }
        defaults.update(kwargs)
        return Notification.objects.create(**defaults)


class NotificationListTests(NotifViewMixin, APITestCase):
    URL = "/api/v1/notifications"

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_lists_own_notifications(self):
        self._make_notif()
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    def test_excludes_other_users_notifications(self):
        self._make_notif(user=self.bob)
        resp = self.client.get(self.URL)
        self.assertEqual(len(resp.data), 0)

    def test_filter_unread(self):
        self._make_notif()
        self._make_notif(read_at=timezone.now())
        resp = self.client.get(self.URL, {"filter": "unread"})
        self.assertEqual(len(resp.data), 1)

    def test_filter_by_origin(self):
        self._make_notif(origin="chat")
        self._make_notif(origin="files")
        resp = self.client.get(self.URL, {"origin": "files"})
        self.assertEqual(len(resp.data), 1)

    def test_search(self):
        self._make_notif(title="Deploy finished")
        self._make_notif(title="New message")
        resp = self.client.get(self.URL, {"search": "deploy"})
        self.assertEqual(len(resp.data), 1)

    def test_search_title_ignores_accents_and_case(self):
        self._make_notif(title="Événement annulé")
        self._make_notif(title="New message")
        resp = self.client.get(self.URL, {"search": "evenement"})
        self.assertEqual([n["title"] for n in resp.data], ["Événement annulé"])

    def test_search_body_ignores_accents_and_case(self):
        self._make_notif(title="Reminder", body="Réunion à 14h")
        self._make_notif(title="Other", body="Nothing here")
        resp = self.client.get(self.URL, {"search": "REUNION"})
        self.assertEqual([n["title"] for n in resp.data], ["Reminder"])

    def test_search_accented_query_matches_unaccented_text(self):
        self._make_notif(title="Elodie shared a file")
        resp = self.client.get(self.URL, {"search": "élodie"})
        self.assertEqual(len(resp.data), 1)


class NotificationListPaginationTests(NotifViewMixin, APITestCase):
    URL = "/api/v1/notifications"

    def _make_dated(self, count):
        for i in range(count):
            notif = self._make_notif(title=f"Notif {i}")
            # auto_now_add ignores a value passed to create().
            Notification.objects.filter(pk=notif.pk).update(
                created_at=f"2024-01-01T10:{i:02d}:00Z"
            )

    def _titles(self, resp):
        return [n["title"] for n in resp.data]

    def test_pages_newest_first_with_offset(self):
        self._make_dated(5)
        first = self.client.get(self.URL, {"limit": 2})
        second = self.client.get(self.URL, {"limit": 2, "offset": 2})
        last = self.client.get(self.URL, {"limit": 2, "offset": 4})
        self.assertEqual(self._titles(first), ["Notif 4", "Notif 3"])
        self.assertEqual(self._titles(second), ["Notif 2", "Notif 1"])
        self.assertEqual(self._titles(last), ["Notif 0"])
        self.assertEqual(first["X-Has-More"], "true")
        self.assertEqual(second["X-Has-More"], "true")
        self.assertEqual(last["X-Has-More"], "false")

    def test_without_limit_lists_everything(self):
        self._make_dated(3)
        resp = self.client.get(self.URL)
        self.assertEqual(len(resp.data), 3)
        self.assertNotIn("X-Has-More", resp)

    def test_invalid_limit_answers_a_page(self):
        """Regression: ?limit=abc raised ValueError and ?limit=-5 a negative
        slice, both a 500; ?limit=0 answered an empty page claiming more."""
        self._make_dated(3)
        for limit in ("abc", "-5", "0", "-1"):
            with self.subTest(limit=limit):
                resp = self.client.get(self.URL, {"limit": limit})
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
                self.assertEqual(len(resp.data), 3)

    def test_invalid_offset_starts_at_the_top(self):
        self._make_dated(3)
        for offset in ("abc", "-5"):
            with self.subTest(offset=offset):
                resp = self.client.get(self.URL, {"limit": 2, "offset": offset})
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
                self.assertEqual(self._titles(resp), ["Notif 2", "Notif 1"])

    def test_limit_is_capped(self):
        self._make_dated(3)
        with patch.object(OptInLimitOffsetPagination, "max_limit", 2):
            resp = self.client.get(self.URL, {"limit": 50})
        self.assertEqual(len(resp.data), 2)
        self.assertEqual(resp["X-Has-More"], "true")


class NotificationDetailTests(NotifViewMixin, APITestCase):
    def _url(self, notif_id):
        return f"/api/v1/notifications/{notif_id}"

    def test_mark_as_read(self):
        notif = self._make_notif()
        resp = self.client.patch(self._url(notif.pk))
        self.assertEqual(resp.status_code, 200)
        notif.refresh_from_db()
        self.assertIsNotNone(notif.read_at)

    def test_mark_already_read_is_idempotent(self):
        notif = self._make_notif(read_at=timezone.now())
        resp = self.client.patch(self._url(notif.pk))
        self.assertEqual(resp.status_code, 200)

    def test_cannot_mark_other_users_notification(self):
        notif = self._make_notif(user=self.bob)
        resp = self.client.patch(self._url(notif.pk))
        self.assertEqual(resp.status_code, 404)

    def test_delete_notification(self):
        notif = self._make_notif()
        resp = self.client.delete(self._url(notif.pk))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Notification.objects.filter(pk=notif.pk).exists())

    def test_delete_nonexistent_returns_404(self):
        import uuid

        resp = self.client.delete(self._url(uuid.uuid4()))
        self.assertEqual(resp.status_code, 404)

    def test_cannot_delete_other_users_notification(self):
        notif = self._make_notif(user=self.bob)
        resp = self.client.delete(self._url(notif.pk))
        self.assertEqual(resp.status_code, 404)


class NotificationReadAllTests(NotifViewMixin, APITestCase):
    URL = "/api/v1/notifications/read-all"

    def test_marks_all_as_read(self):
        self._make_notif(title="N1")
        self._make_notif(title="N2")
        resp = self.client.post(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["marked"], 2)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.alice, read_at__isnull=True
            ).count(),
            0,
        )

    def test_does_not_affect_other_users(self):
        self._make_notif(user=self.bob)
        resp = self.client.post(self.URL)
        self.assertEqual(resp.data["marked"], 0)
        self.assertEqual(
            Notification.objects.filter(
                recipient=self.bob, read_at__isnull=True
            ).count(),
            1,
        )
