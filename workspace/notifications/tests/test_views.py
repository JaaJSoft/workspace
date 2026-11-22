import uuid
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

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
        self.assertEqual(len(resp.data["notifications"]), 1)

    def test_excludes_other_users_notifications(self):
        self._make_notif(user=self.bob)
        resp = self.client.get(self.URL)
        self.assertEqual(len(resp.data["notifications"]), 0)

    def test_filter_unread(self):
        self._make_notif()
        self._make_notif(read_at=timezone.now())
        resp = self.client.get(self.URL, {"filter": "unread"})
        self.assertEqual(len(resp.data["notifications"]), 1)

    def test_filter_by_origin(self):
        self._make_notif(origin="chat")
        self._make_notif(origin="files")
        resp = self.client.get(self.URL, {"origin": "files"})
        self.assertEqual(len(resp.data["notifications"]), 1)

    def test_search(self):
        self._make_notif(title="Deploy finished")
        self._make_notif(title="New message")
        resp = self.client.get(self.URL, {"search": "deploy"})
        self.assertEqual(len(resp.data["notifications"]), 1)

    def test_search_title_ignores_accents_and_case(self):
        self._make_notif(title="Événement annulé")
        self._make_notif(title="New message")
        resp = self.client.get(self.URL, {"search": "evenement"})
        self.assertEqual(
            [n["title"] for n in resp.data["notifications"]], ["Événement annulé"]
        )

    def test_search_body_ignores_accents_and_case(self):
        self._make_notif(title="Reminder", body="Réunion à 14h")
        self._make_notif(title="Other", body="Nothing here")
        resp = self.client.get(self.URL, {"search": "REUNION"})
        self.assertEqual([n["title"] for n in resp.data["notifications"]], ["Reminder"])

    def test_search_accented_query_matches_unaccented_text(self):
        self._make_notif(title="Elodie shared a file")
        resp = self.client.get(self.URL, {"search": "élodie"})
        self.assertEqual(len(resp.data["notifications"]), 1)

    def test_includes_unread_count(self):
        self._make_notif()
        resp = self.client.get(self.URL)
        self.assertIn("unread_count", resp.data)

    def test_has_more_pagination(self):
        for i in range(25):
            self._make_notif(title=f"Notif {i}")
        resp = self.client.get(self.URL, {"limit": 5})
        self.assertTrue(resp.data["has_more"])
        self.assertEqual(len(resp.data["notifications"]), 5)

    def test_last_page_has_no_cursor(self):
        self._make_notif()
        resp = self.client.get(self.URL, {"limit": 5})
        self.assertFalse(resp.data["has_more"])
        self.assertIsNone(resp.data["next_cursor"])

    def test_zero_limit_returns_one_row(self):
        self._make_notif()
        self._make_notif()
        resp = self.client.get(self.URL, {"limit": 0})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["notifications"]), 1)

    def test_malformed_cursor_is_refused(self):
        self._make_notif()
        for raw in (
            "not-a-cursor",
            "123",
            f"123.{uuid.uuid4()}x",
            f"9{'9' * 19}.{uuid.uuid4()}",
        ):
            with self.subTest(raw=raw):
                resp = self.client.get(self.URL, {"cursor": raw})
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class NotificationCursorTests(NotifViewMixin, APITestCase):
    """Load more must never hand back a row the client already has."""

    URL = "/api/v1/notifications"

    def _make_at(self, title, created_at, **kwargs):
        notif = self._make_notif(title=title, **kwargs)
        # auto_now_add ignores an assigned value on insert.
        Notification.objects.filter(pk=notif.pk).update(created_at=created_at)
        return notif

    def _make_four(self, **kwargs):
        return [
            self._make_at(f"n{i}", datetime(2024, 1, 1, 10 + i, tzinfo=UTC), **kwargs)
            for i in range(4)
        ]

    def _titles(self, resp):
        return [n["title"] for n in resp.data["notifications"]]

    def _first_page(self, **params):
        resp = self.client.get(self.URL, {"limit": 2, **params})
        self.assertEqual(self._titles(resp), ["n3", "n2"])
        return resp.data["next_cursor"]

    def test_cursor_row_merged_to_the_present(self):
        rows = self._make_four()
        cursor = self._first_page()
        # notify_stream bumps created_at of the row it merges into.
        Notification.objects.filter(pk=rows[2].pk).update(created_at=timezone.now())

        resp = self.client.get(self.URL, {"limit": 2, "cursor": cursor})
        self.assertEqual(self._titles(resp), ["n1", "n0"])

    def test_cursor_row_read_under_the_unread_filter(self):
        rows = self._make_four()
        cursor = self._first_page(filter="unread")
        Notification.objects.filter(pk=rows[2].pk).update(read_at=timezone.now())

        resp = self.client.get(
            self.URL, {"limit": 2, "filter": "unread", "cursor": cursor}
        )
        self.assertEqual(self._titles(resp), ["n1", "n0"])

    def test_cursor_row_deleted(self):
        rows = self._make_four()
        cursor = self._first_page()
        rows[2].delete()

        resp = self.client.get(self.URL, {"limit": 2, "cursor": cursor})
        self.assertEqual(self._titles(resp), ["n1", "n0"])

    def test_rows_sharing_a_timestamp_are_each_listed_once(self):
        instant = datetime(2024, 1, 1, 10, tzinfo=UTC)
        expected = {self._make_at(f"n{i}", instant).uuid for i in range(5)}

        seen = []
        params = {"limit": 2}
        while True:
            resp = self.client.get(self.URL, params)
            seen += [n["uuid"] for n in resp.data["notifications"]]
            if not resp.data["has_more"]:
                break
            params["cursor"] = resp.data["next_cursor"]

        self.assertEqual(len(seen), len(expected))
        self.assertEqual({uuid.UUID(str(u)) for u in seen}, expected)


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
