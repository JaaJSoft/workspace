"""Real VAPID key material for the push tests.

Generated at import rather than hard-coded so no private key literal lives in
the repository. Tests that mock ``webpush`` still need a loadable key: the task
parses it before reaching the send loop, so a placeholder string would make the
task bail out early and the mock never fire.
"""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def generate_keypair():
    """Return ``(pem, der_b64, raw_b64, public_b64)`` for one fresh EC key."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = (
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
        .strip()
    )
    der_b64 = "".join(pem.splitlines()[1:-1])
    raw_b64 = b64(key.private_numbers().private_value.to_bytes(32, "big"))
    public_b64 = b64(
        key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )
    return pem, der_b64, raw_b64, public_b64


def subscription_keys():
    """A well-formed ``(p256dh, auth)`` pair so payload encryption runs."""
    peer = ec.generate_private_key(ec.SECP256R1())
    p256dh = b64(
        peer.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )
    return p256dh, b64(b"0123456789abcdef")


TEST_PRIVATE_KEY_PEM, _, TEST_PRIVATE_KEY_RAW, TEST_PUBLIC_KEY = generate_keypair()
