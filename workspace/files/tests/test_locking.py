from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File

User = get_user_model()


class FileLockAPITests(APITestCase):
    """Tests for file locking API endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='alice@test.com', password='pass123',
        )
        self.other = User.objects.create_user(
            username='bob', email='bob@test.com', password='pass123',
        )
        self.file = File.objects.create(
            owner=self.user, name='doc.md', node_type=File.NodeType.FILE,
        )

    def _url(self, uuid=None):
        return f'/api/v1/files/{uuid or self.file.uuid}/lock'

    # ── POST (acquire / renew) ───────────────────────────

    def test_acquire_lock(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.file.refresh_from_db()
        self.assertEqual(self.file.locked_by, self.user)
        self.assertIsNotNone(self.file.lock_expires_at)

    def test_renew_own_lock(self):
        self.client.force_authenticate(self.user)
        self.client.post(self._url())
        self.file.refresh_from_db()
        old_expires = self.file.lock_expires_at

        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.file.refresh_from_db()
        self.assertGreaterEqual(self.file.lock_expires_at, old_expires)

    def test_acquire_conflict(self):
        """Another user cannot acquire a lock held by someone else."""
        self.client.force_authenticate(self.user)
        self.client.post(self._url())

        self.client.force_authenticate(self.other)
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('locked_by', resp.data)

    def test_acquire_expired_lock(self):
        """Can acquire a lock that has expired."""
        File.objects.filter(pk=self.file.pk).update(
            locked_by=self.user,
            locked_at=timezone.now() - timedelta(minutes=10),
            lock_expires_at=timezone.now() - timedelta(minutes=5),
        )
        self.client.force_authenticate(self.other)
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.file.refresh_from_db()
        self.assertEqual(self.file.locked_by, self.other)

    # ── DELETE (release) ─────────────────────────────────

    def test_release_own_lock(self):
        self.client.force_authenticate(self.user)
        self.client.post(self._url())
        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.file.refresh_from_db()
        self.assertIsNone(self.file.locked_by_id)

    def test_force_unlock_by_other_user(self):
        """Any user with access can force-release a lock."""
        self.client.force_authenticate(self.user)
        self.client.post(self._url())

        self.client.force_authenticate(self.other)
        resp = self.client.delete(self._url())
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.file.refresh_from_db()
        self.assertIsNone(self.file.locked_by_id)

    # ── GET (info) ───────────────────────────────────────

    def test_get_lock_info_unlocked(self):
        self.client.force_authenticate(self.user)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data['locked_by'])

    def test_get_lock_info_locked(self):
        self.client.force_authenticate(self.user)
        self.client.post(self._url())
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['locked_by']['username'], 'alice')
        self.assertFalse(resp.data['is_expired'])

    # ── Save protection ──────────────────────────────────

    def test_save_blocked_when_locked_by_other(self):
        """PATCH returns 423 when file is locked by another user."""
        self.client.force_authenticate(self.user)
        self.client.post(self._url())

        self.client.force_authenticate(self.other)
        resp = self.client.patch(
            f'/api/v1/files/{self.file.uuid}',
            {'name': 'renamed.md'},
        )
        self.assertEqual(resp.status_code, status.HTTP_423_LOCKED)

    def test_save_allowed_for_lock_owner(self):
        """PATCH succeeds for the user who holds the lock."""
        self.client.force_authenticate(self.user)
        self.client.post(self._url())

        resp = self.client.patch(
            f'/api/v1/files/{self.file.uuid}',
            {'name': 'renamed.md'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_save_allowed_when_unlocked(self):
        """PATCH succeeds when no lock exists."""
        self.client.force_authenticate(self.user)
        resp = self.client.patch(
            f'/api/v1/files/{self.file.uuid}',
            {'name': 'renamed.md'},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # ── Edge cases ───────────────────────────────────────

    def test_lock_nonexistent_file(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post(self._url('00000000-0000-0000-0000-000000000000'))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_lock_requires_authentication(self):
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
