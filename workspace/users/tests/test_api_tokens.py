from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from knox.models import AuthToken
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.users.models import APITokenLabel

User = get_user_model()

LIST_CREATE_URL = '/api/v1/auth/tokens'


def detail_url(pk):
    return f'/api/v1/auth/tokens/{pk}'


class APITokenTestMixin:
    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', password='Str0ngP@ss!',
        )
        self.client.force_authenticate(self.user)

    def _create_token(self, name='test', expiry=None):
        """Helper: create a token via knox and attach a label."""
        instance, raw = AuthToken.objects.create(user=self.user, expiry=expiry)
        APITokenLabel.objects.create(auth_token=instance, name=name)
        return instance, raw


# ── List ────────────────────────────────────────────────────────

class ListTokensTests(APITokenTestMixin, APITestCase):

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.get(LIST_CREATE_URL)
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_empty_list(self):
        resp = self.client.get(LIST_CREATE_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])

    def test_lists_own_tokens(self):
        self._create_token(name='tok1')
        self._create_token(name='tok2')
        resp = self.client.get(LIST_CREATE_URL)
        self.assertEqual(len(resp.data), 2)
        names = {t['name'] for t in resp.data}
        self.assertEqual(names, {'tok1', 'tok2'})

    def test_does_not_list_other_users_tokens(self):
        other = User.objects.create_user(username='bob', password='pass')
        AuthToken.objects.create(user=other)
        self._create_token(name='mine')
        resp = self.client.get(LIST_CREATE_URL)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['name'], 'mine')

    def test_excludes_expired_tokens(self):
        self._create_token(name='active')
        # Create an already-expired token
        instance, _ = AuthToken.objects.create(
            user=self.user, expiry=timedelta(seconds=1),
        )
        APITokenLabel.objects.create(auth_token=instance, name='expired')
        instance.expiry = timezone.now() - timedelta(hours=1)
        instance.save(update_fields=['expiry'])

        resp = self.client.get(LIST_CREATE_URL)
        names = [t['name'] for t in resp.data]
        self.assertIn('active', names)
        self.assertNotIn('expired', names)

    def test_does_not_expose_full_token(self):
        _, raw = self._create_token()
        resp = self.client.get(LIST_CREATE_URL)
        self.assertNotIn('token', resp.data[0])
        self.assertIn('token_key', resp.data[0])
        # token_key is a short prefix, much shorter than the full token
        self.assertLess(len(resp.data[0]['token_key']), len(raw))

    def test_response_fields(self):
        self._create_token(name='mytoken')
        resp = self.client.get(LIST_CREATE_URL)
        token = resp.data[0]
        self.assertIn('id', token)
        self.assertIn('name', token)
        self.assertIn('token_key', token)
        self.assertIn('created', token)
        self.assertIn('expiry', token)


# ── Create ──────────────────────────────────────────────────────

class CreateTokenTests(APITokenTestMixin, APITestCase):

    def test_unauthenticated_rejected(self):
        self.client.force_authenticate(None)
        resp = self.client.post(LIST_CREATE_URL, {})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_minimal(self):
        resp = self.client.post(LIST_CREATE_URL, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIn('token', resp.data)
        self.assertIn('token_key', resp.data)
        self.assertIsNone(resp.data['expiry'])

    def test_create_with_name(self):
        resp = self.client.post(
            LIST_CREATE_URL, {'name': 'CI bot'}, format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data['name'], 'CI bot')
        # Verify label was persisted
        label = APITokenLabel.objects.get(auth_token_id=resp.data['id'])
        self.assertEqual(label.name, 'CI bot')

    def test_create_with_expiry(self):
        resp = self.client.post(
            LIST_CREATE_URL, {'expiry_days': 30}, format='json',
        )
        self.assertEqual(resp.status_code, 201)
        self.assertIsNotNone(resp.data['expiry'])

    def test_create_invalid_expiry_zero(self):
        resp = self.client.post(
            LIST_CREATE_URL, {'expiry_days': 0}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_invalid_expiry_negative(self):
        resp = self.client.post(
            LIST_CREATE_URL, {'expiry_days': -5}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_create_invalid_expiry_string(self):
        resp = self.client.post(
            LIST_CREATE_URL, {'expiry_days': 'abc'}, format='json',
        )
        self.assertEqual(resp.status_code, 400)

    def test_returned_token_authenticates(self):
        resp = self.client.post(
            LIST_CREATE_URL, {'name': 'auth-test'}, format='json',
        )
        raw_token = resp.data['token']

        # Use the token to call a protected endpoint
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {raw_token}')
        me_resp = self.client.get('/api/v1/users/me')
        self.assertEqual(me_resp.status_code, 200)
        self.assertEqual(me_resp.data['username'], 'alice')


# ── Revoke ──────────────────────────────────────────────────────

class RevokeTokenTests(APITokenTestMixin, APITestCase):

    def test_unauthenticated_rejected(self):
        instance, _ = self._create_token()
        self.client.force_authenticate(None)
        resp = self.client.delete(detail_url(instance.pk))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_revoke_own_token(self):
        instance, _ = self._create_token()
        resp = self.client.delete(detail_url(instance.pk))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(AuthToken.objects.filter(pk=instance.pk).exists())

    def test_revoke_nonexistent_returns_404(self):
        resp = self.client.delete(detail_url(99999))
        self.assertEqual(resp.status_code, 404)

    def test_cannot_revoke_other_users_token(self):
        other = User.objects.create_user(username='bob', password='pass')
        instance, _ = AuthToken.objects.create(user=other)
        resp = self.client.delete(detail_url(instance.pk))
        self.assertEqual(resp.status_code, 404)
        # Token still exists
        self.assertTrue(AuthToken.objects.filter(pk=instance.pk).exists())

    def test_revoked_token_no_longer_authenticates(self):
        instance, raw = self._create_token()

        # Revoke
        self.client.delete(detail_url(instance.pk))

        # Try to use it
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {raw}')
        resp = self.client.get('/api/v1/users/me')
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_revoke_cascades_label(self):
        instance, _ = self._create_token(name='to-delete')
        self.assertEqual(APITokenLabel.objects.count(), 1)
        self.client.delete(detail_url(instance.pk))
        self.assertEqual(APITokenLabel.objects.count(), 0)
