from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.notifications.models import Notification

User = get_user_model()


class NotifViewMixin:
    def setUp(self):
        self.alice = User.objects.create_user(username='alice', password='pass')
        self.bob = User.objects.create_user(username='bob', password='pass')
        self.client.force_authenticate(self.alice)

    def _make_notif(self, user=None, **kwargs):
        defaults = {
            'recipient': user or self.alice,
            'origin': 'chat', 'icon': 'msg', 'title': 'Test',
        }
        defaults.update(kwargs)
        return Notification.objects.create(**defaults)


class NotificationListTests(NotifViewMixin, APITestCase):
    URL = '/api/v1/notifications'

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_lists_own_notifications(self):
        self._make_notif()
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['notifications']), 1)

    def test_excludes_other_users_notifications(self):
        self._make_notif(user=self.bob)
        resp = self.client.get(self.URL)
        self.assertEqual(len(resp.data['notifications']), 0)

    def test_filter_unread(self):
        self._make_notif()
        self._make_notif(read_at=timezone.now())
        resp = self.client.get(self.URL, {'filter': 'unread'})
        self.assertEqual(len(resp.data['notifications']), 1)

    def test_filter_by_origin(self):
        self._make_notif(origin='chat')
        self._make_notif(origin='files')
        resp = self.client.get(self.URL, {'origin': 'files'})
        self.assertEqual(len(resp.data['notifications']), 1)

    def test_search(self):
        self._make_notif(title='Deploy finished')
        self._make_notif(title='New message')
        resp = self.client.get(self.URL, {'search': 'deploy'})
        self.assertEqual(len(resp.data['notifications']), 1)

    def test_includes_unread_count(self):
        self._make_notif()
        resp = self.client.get(self.URL)
        self.assertIn('unread_count', resp.data)

    def test_has_more_pagination(self):
        for i in range(25):
            self._make_notif(title=f'Notif {i}')
        resp = self.client.get(self.URL, {'limit': 5})
        self.assertTrue(resp.data['has_more'])
        self.assertEqual(len(resp.data['notifications']), 5)


class NotificationDetailTests(NotifViewMixin, APITestCase):

    def _url(self, notif_id):
        return f'/api/v1/notifications/{notif_id}'

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
    URL = '/api/v1/notifications/read-all'

    def test_marks_all_as_read(self):
        self._make_notif(title='N1')
        self._make_notif(title='N2')
        resp = self.client.post(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['marked'], 2)
        self.assertEqual(
            Notification.objects.filter(recipient=self.alice, read_at__isnull=True).count(), 0,
        )

    def test_does_not_affect_other_users(self):
        self._make_notif(user=self.bob)
        resp = self.client.post(self.URL)
        self.assertEqual(resp.data['marked'], 0)
        self.assertEqual(
            Notification.objects.filter(recipient=self.bob, read_at__isnull=True).count(), 1,
        )
