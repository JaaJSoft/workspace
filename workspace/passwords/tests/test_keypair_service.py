"""Unit tests for KeyPairService.

Covers every public method in workspace.passwords.services.keypair:
  - create_or_update_keypair
  - get_public_key
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.passwords.models import UserKeyPair
from workspace.passwords.services.keypair import KeyPairService

User = get_user_model()

_SALT = 'A' * 43  # valid 43-char base64url salt (32 bytes without padding)


class KeyPairServiceTests(TestCase):

    def setUp(self):
        self.alice = User.objects.create_user(username='alice_kp', email='alice_kp@test.com', password='pass')
        self.bob = User.objects.create_user(username='bob_kp', email='bob_kp@test.com', password='pass')

    # ------------------------------------------------------------------
    # create_or_update_keypair
    # ------------------------------------------------------------------

    def test_creates_keypair(self):
        kp = KeyPairService.create_or_update_keypair(self.alice, 'pub-key', 'enc-priv-key', _SALT)
        self.assertIsInstance(kp, UserKeyPair)
        self.assertEqual(kp.user, self.alice)
        self.assertEqual(kp.public_key, 'pub-key')
        self.assertEqual(kp.protected_private_key, 'enc-priv-key')

    def test_default_algorithm_is_ecdh_p256(self):
        kp = KeyPairService.create_or_update_keypair(self.alice, 'pub', 'priv', _SALT)
        self.assertEqual(kp.algorithm, 'ecdh_p256')

    def test_stores_provided_kdf_salt(self):
        kp = KeyPairService.create_or_update_keypair(self.alice, 'pub', 'priv', _SALT)
        self.assertEqual(kp.kdf_salt, _SALT)

    def test_generates_kdf_salt_when_not_provided(self):
        kp = KeyPairService.create_or_update_keypair(self.alice, 'pub', 'priv')
        self.assertTrue(kp.kdf_salt)
        self.assertLessEqual(len(kp.kdf_salt), 44)

    def test_update_replaces_existing_keypair(self):
        KeyPairService.create_or_update_keypair(self.alice, 'old-pub', 'old-priv', _SALT)
        kp = KeyPairService.create_or_update_keypair(self.alice, 'new-pub', 'new-priv', 'B' * 43)
        self.assertEqual(kp.public_key, 'new-pub')
        self.assertEqual(kp.protected_private_key, 'new-priv')

    def test_update_does_not_create_duplicate(self):
        KeyPairService.create_or_update_keypair(self.alice, 'pub1', 'priv1', _SALT)
        KeyPairService.create_or_update_keypair(self.alice, 'pub2', 'priv2', _SALT)
        self.assertEqual(UserKeyPair.objects.filter(user=self.alice).count(), 1)

    def test_keypairs_are_isolated_per_user(self):
        KeyPairService.create_or_update_keypair(self.alice, 'alice-pub', 'alice-priv', _SALT)
        KeyPairService.create_or_update_keypair(self.bob, 'bob-pub', 'bob-priv', 'B' * 43)
        alice_kp = UserKeyPair.objects.get(user=self.alice)
        bob_kp = UserKeyPair.objects.get(user=self.bob)
        self.assertEqual(alice_kp.public_key, 'alice-pub')
        self.assertEqual(bob_kp.public_key, 'bob-pub')

    def test_keypair_is_persisted(self):
        kp = KeyPairService.create_or_update_keypair(self.alice, 'pub', 'priv', _SALT)
        from_db = UserKeyPair.objects.get(pk=kp.pk)
        self.assertEqual(from_db.public_key, 'pub')

    # ------------------------------------------------------------------
    # get_public_key
    # ------------------------------------------------------------------

    def test_get_public_key_returns_public_key(self):
        KeyPairService.create_or_update_keypair(self.alice, 'my-pub-key', 'priv', _SALT)
        alice = User.objects.get(pk=self.alice.pk)
        self.assertEqual(KeyPairService.get_public_key(alice), 'my-pub-key')

    def test_get_public_key_returns_none_for_user_without_keypair(self):
        self.assertIsNone(KeyPairService.get_public_key(self.bob))

    def test_get_public_key_reflects_updated_key(self):
        KeyPairService.create_or_update_keypair(self.alice, 'old-pub', 'priv', _SALT)
        KeyPairService.create_or_update_keypair(self.alice, 'new-pub', 'priv', _SALT)
        alice = User.objects.get(pk=self.alice.pk)
        self.assertEqual(KeyPairService.get_public_key(alice), 'new-pub')
