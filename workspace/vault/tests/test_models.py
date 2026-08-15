from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from workspace.vault.models import AccountIdentity

User = get_user_model()


def make_identity(user, **overrides):
    """An identity with placeholder blobs - the server never reads them."""
    fields = {
        "user": user,
        "kdf_params": {"v": "1.3", "m": 65536, "t": 3, "p": 2},
        "kdf_salt": "c2FsdA",
        "kex_public": "AWtleA",
        "sig_public": "AXNpZw",
        "wrapped_kex_priv": "AQEAAAAMd3JhcHBlZC1rZXg",
        "wrapped_sig_priv": "AQEAAAAMd3JhcHBlZC1zaWc",
        "sig_over_kex_pub": "AXNpZ25hdHVyZQ",
    }
    fields.update(overrides)
    return AccountIdentity.objects.create(**fields)


class AccountIdentityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="owner", password="pw")

    def test_defaults_to_argon2id_and_pending(self):
        identity = make_identity(self.user)
        self.assertEqual(identity.kdf_algo, "argon2id")
        self.assertEqual(identity.state, AccountIdentity.State.PENDING)

    def test_one_identity_per_user(self):
        make_identity(self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_identity(self.user)

    def test_reachable_from_the_user(self):
        identity = make_identity(self.user)
        self.user.refresh_from_db()
        self.assertEqual(self.user.vault_identity, identity)

    def test_deleted_with_its_user(self):
        make_identity(self.user)
        self.user.delete()
        self.assertEqual(AccountIdentity.objects.count(), 0)
