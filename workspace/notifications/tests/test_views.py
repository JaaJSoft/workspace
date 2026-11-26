import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.notifications.models import Notification
from workspace.notifications.services.notifications import get_unread_count

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

    def _mark_read(self, notif, is_read=True):
        return self.client.patch(
            f"/api/v1/notifications/{notif.pk}", {"is_read": is_read}, format="json"
        )


class NotificationListTests(NotifViewMixin, APITestCase):
    URL = "/api/v1/notifications"

    def _titles(self, **params):
        return sorted(n["title"] for n in self.client.get(self.URL, params).data)

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

    def test_a_notification_carries_its_actor_as_a_user_summary(self):
        self._make_notif(title="From Bob", actor=self.bob)
        self._make_notif(title="System")
        by_title = {n["title"]: n for n in self.client.get(self.URL).data}
        self.assertEqual(
            by_title["From Bob"]["actor"],
            {
                "id": self.bob.pk,
                "username": "bob",
                "first_name": "",
                "last_name": "",
                "avatar_url": f"/api/v1/users/{self.bob.pk}/avatar",
            },
        )
        self.assertIsNone(by_title["System"]["actor"])

    def test_read_filter_has_three_states(self):
        self._make_notif(title="Unread")
        self._make_notif(title="Read", read_at=timezone.now())
        self.assertEqual(self._titles(), ["Read", "Unread"])
        self.assertEqual(self._titles(read="false"), ["Unread"])
        self.assertEqual(self._titles(read="true"), ["Read"])

    def test_an_unreadable_read_filter_is_refused(self):
        resp = self.client.get(self.URL, {"read": "maybe"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(list(resp.data), ["read"])

    def test_an_unknown_parameter_is_ignored(self):
        self._make_notif()
        resp = self.client.get(self.URL, {"unraed": "true"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)

    def test_filter_by_origin(self):
        self._make_notif(origin="chat")
        self._make_notif(origin="files")
        resp = self.client.get(self.URL, {"origin": "files"})
        self.assertEqual([n["origin"] for n in resp.data], ["files"])

    def test_filter_by_several_origins(self):
        for origin in ("chat", "files", "mail"):
            self._make_notif(origin=origin)
        repeated = self.client.get(f"{self.URL}?origin=files&origin=mail")
        joined = self.client.get(self.URL, {"origin": "files,mail"})
        self.assertEqual(sorted(n["origin"] for n in repeated.data), ["files", "mail"])
        self.assertEqual(repeated.data, joined.data)

    def test_search(self):
        self._make_notif(title="Deploy finished")
        self._make_notif(title="New message")
        self.assertEqual(self._titles(q="deploy"), ["Deploy finished"])

    def test_search_title_ignores_accents_and_case(self):
        self._make_notif(title="Événement annulé")
        self._make_notif(title="New message")
        self.assertEqual(self._titles(q="evenement"), ["Événement annulé"])

    def test_search_body_ignores_accents_and_case(self):
        self._make_notif(title="Reminder", body="Réunion à 14h")
        self._make_notif(title="Other", body="Nothing here")
        self.assertEqual(self._titles(q="REUNION"), ["Reminder"])

    def test_search_accented_query_matches_unaccented_text(self):
        self._make_notif(title="Elodie shared a file")
        self.assertEqual(self._titles(q="élodie"), ["Elodie shared a file"])

    def test_filters_combine(self):
        self._make_notif(title="Deploy done", origin="files")
        self._make_notif(title="Deploy seen", origin="files", read_at=timezone.now())
        self._make_notif(title="Deploy elsewhere", origin="chat")
        self.assertEqual(
            self._titles(q="deploy", origin="files", read="false"), ["Deploy done"]
        )


class NotificationListPaginationTests(NotifViewMixin, APITestCase):
    URL = "/api/v1/notifications"

    def _make_dated(self, count):
        made = []
        for i in range(count):
            notif = self._make_notif(title=f"Notif {i}")
            # auto_now_add ignores a value passed to create().
            Notification.objects.filter(pk=notif.pk).update(
                created_at=f"2024-01-01T10:{i:02d}:00Z"
            )
            made.append(notif)
        return made

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

    def test_rows_created_at_the_same_instant_are_each_listed_once(self):
        notifs = self._make_dated(5)
        Notification.objects.update(created_at="2024-01-01T10:00:00Z")
        listed = []
        for offset in (0, 2, 4):
            page = self.client.get(self.URL, {"limit": 2, "offset": offset})
            listed += [n["uuid"] for n in page.data]
        self.assertEqual(sorted(listed), sorted(str(n.pk) for n in notifs))

    def test_without_limit_the_page_holds_fifty(self):
        Notification.objects.bulk_create(
            Notification(recipient=self.alice, origin="chat", icon="msg", title=f"N{i}")
            for i in range(51)
        )
        resp = self.client.get(self.URL)
        self.assertEqual(len(resp.data), 50)
        self.assertEqual(resp["X-Has-More"], "true")

    def test_invalid_page_parameters_are_refused(self):
        """Regression: ?limit=abc used to list every row instead of saying so."""
        self._make_dated(1)
        for params in (
            {"limit": "abc"},
            {"limit": "0"},
            {"limit": "-5"},
            {"limit": "1001"},
            {"offset": "abc"},
            {"offset": "-5"},
            {"count": "maybe"},
        ):
            with self.subTest(params=params):
                resp = self.client.get(self.URL, params)
                self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(list(resp.data), list(params))

    def test_count_is_answered_on_demand_only(self):
        self._make_dated(3)
        self._make_notif(user=self.bob)
        plain = self.client.get(self.URL, {"limit": 2})
        counted = self.client.get(self.URL, {"limit": 2, "count": "true"})
        self.assertNotIn("X-Total-Count", plain)
        self.assertEqual(counted["X-Total-Count"], "3")


class NotificationDetailTests(NotifViewMixin, APITestCase):
    def _url(self, notif_id):
        return f"/api/v1/notifications/{notif_id}"

    def test_get_one(self):
        notif = self._make_notif(title="Hello")
        resp = self.client.get(self._url(notif.pk))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["title"], "Hello")
        self.assertIs(resp.data["is_read"], False)

    def test_mark_as_read(self):
        notif = self._make_notif()
        resp = self._mark_read(notif)
        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.data["is_read"], True)
        notif.refresh_from_db()
        self.assertIsNotNone(notif.read_at)

    def test_mark_already_read_is_idempotent(self):
        read_at = timezone.now()
        notif = self._make_notif(read_at=read_at)
        resp = self._mark_read(notif)
        self.assertEqual(resp.status_code, 200)
        notif.refresh_from_db()
        self.assertEqual(notif.read_at, read_at)

    def test_mark_as_unread(self):
        notif = self._make_notif(read_at=timezone.now())
        resp = self._mark_read(notif, False)
        self.assertEqual(resp.status_code, 200)
        self.assertIs(resp.data["is_read"], False)
        notif.refresh_from_db()
        self.assertIsNone(notif.read_at)

    def test_an_empty_patch_changes_nothing(self):
        notif = self._make_notif()
        resp = self.client.patch(self._url(notif.pk), {}, format="json")
        self.assertEqual(resp.status_code, 200)
        notif.refresh_from_db()
        self.assertIsNone(notif.read_at)

    def test_a_patch_cannot_rewrite_the_notification(self):
        notif = self._make_notif(title="Original")
        self.client.patch(
            self._url(notif.pk), {"title": "Forged", "url": "/evil"}, format="json"
        )
        notif.refresh_from_db()
        self.assertEqual((notif.title, notif.url), ("Original", ""))

    def test_put_is_not_offered(self):
        notif = self._make_notif()
        resp = self.client.put(self._url(notif.pk), {"is_read": True}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_another_users_notification_answers_like_an_absent_one(self):
        theirs = self._make_notif(user=self.bob)
        for method in ("get", "patch", "delete"):
            with self.subTest(method=method):
                send = getattr(self.client, method)
                hidden = send(self._url(theirs.pk), {"is_read": True}, format="json")
                absent = send(self._url(uuid.uuid4()), {"is_read": True}, format="json")
                self.assertEqual(hidden.status_code, status.HTTP_404_NOT_FOUND)
                self.assertEqual(
                    (hidden.status_code, hidden.content),
                    (absent.status_code, absent.content),
                )
        theirs.refresh_from_db()
        self.assertIsNone(theirs.read_at)

    def test_a_refusal_has_a_body(self):
        resp = self.client.get(self._url(uuid.uuid4()))
        self.assertIn("detail", resp.data)

    def test_delete_notification(self):
        notif = self._make_notif()
        resp = self.client.delete(self._url(notif.pk))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(Notification.objects.filter(pk=notif.pk).exists())

    def test_deleting_an_unread_notification_lowers_the_unread_count(self):
        self.addCleanup(cache.clear)
        notif = self._make_notif()
        self.assertEqual(get_unread_count(self.alice), 1)
        self.client.delete(self._url(notif.pk))
        self.assertEqual(get_unread_count(self.alice), 0)


class NotificationMarkAllReadTests(NotifViewMixin, APITestCase):
    URL = "/api/v1/notifications/mark-all-read"

    def _unread(self, user):
        return Notification.objects.filter(recipient=user, read_at__isnull=True).count()

    def test_marks_all_as_read(self):
        self._make_notif(title="N1")
        self._make_notif(title="N2")
        resp = self.client.post(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(self._unread(self.alice), 0)

    def test_does_not_affect_other_users(self):
        self._make_notif(user=self.bob)
        self.client.post(self.URL)
        self.assertEqual(self._unread(self.bob), 1)


@patch("workspace.notifications.services.notifications.notify_sse")
class NotificationStreamWakeTests(NotifViewMixin, APITestCase):
    """A read or a deletion wakes the recipient's streams, so every other open
    tab shows the new unread count at once."""

    def _woken(self, notify_sse, send):
        with self.captureOnCommitCallbacks(execute=True):
            send()
        return [call.args for call in notify_sse.call_args_list]

    def test_marking_one_read(self, notify_sse):
        notif = self._make_notif()
        woken = self._woken(notify_sse, lambda: self._mark_read(notif))
        self.assertEqual(woken, [("notifications", self.alice.pk)])

    def test_marking_one_unread(self, notify_sse):
        notif = self._make_notif(read_at=timezone.now())
        woken = self._woken(notify_sse, lambda: self._mark_read(notif, False))
        self.assertEqual(woken, [("notifications", self.alice.pk)])

    def test_marking_all_read(self, notify_sse):
        self._make_notif()
        woken = self._woken(
            notify_sse,
            lambda: self.client.post("/api/v1/notifications/mark-all-read"),
        )
        self.assertEqual(woken, [("notifications", self.alice.pk)])

    def test_deleting_one(self, notify_sse):
        notif = self._make_notif()
        woken = self._woken(
            notify_sse, lambda: self.client.delete(f"/api/v1/notifications/{notif.pk}")
        )
        self.assertEqual(woken, [("notifications", self.alice.pk)])

    def test_nothing_changed_wakes_nobody(self, notify_sse):
        notif = self._make_notif(read_at=timezone.now())
        woken = self._woken(notify_sse, lambda: self._mark_read(notif))
        self.assertEqual(woken, [])
