"""Fixtures shared by the API test modules.

Every test that writes a record needs a real Ed25519 key and a real signature -
the views verify one, and a stubbed signature would only prove the stub agrees
with itself. Building that account in one place keeps the payload key sets
under test in the builders rather than copied across files.
"""

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model

from workspace.vault.models import AccountIdentity, Vault
from workspace.vault.services.metadata import canonical_cbor
from workspace.vault.tests.reference.encoding import to_base64url

User = get_user_model()

# The algorithm bytes the wire format prefixes its keys and signatures with.
PUBKEY_ALG_ED25519 = 0x02
SIG_ALG_ED25519 = 0x01

HPKE_SUITE = {"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0}


def make_account(username="owner", password="pw"):
    """A user with an active vault identity, and the private key behind it."""
    user = User.objects.create_user(username=username, password=password)
    signer = Ed25519PrivateKey.generate()
    identity = AccountIdentity.objects.create(
        user=user,
        kdf_salt="SALT",
        state=AccountIdentity.State.ACTIVE,
        sig_public=to_base64url(
            bytes([PUBKEY_ALG_ED25519]) + signer.public_key().public_bytes_raw()
        ),
    )
    return user, signer, identity


def sign(signer, payload):
    """The wire form of *signer*'s signature over a canonically encoded
    payload: one algorithm byte, then the 64 raw bytes."""
    return to_base64url(bytes([SIG_ALG_ED25519]) + signer.sign(canonical_cbor(payload)))


def make_vault(owner, **overrides):
    """A stored vault. Its own signature is not re-verified on read, so the
    bytes only have to be non-empty."""
    fields = {
        "encrypted_name": "AQEBAAABc2VhbGVk",
        "metadata_sig": "AXNpZ25hdHVyZQ",
    }
    fields.update(overrides)
    return Vault.objects.create(owner=owner, **fields)
