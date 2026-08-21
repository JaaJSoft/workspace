"""Server-side verification of the account key attestation.

The server can decrypt nothing. What it can do is refuse to store an identity
that no other client could ever verify: a public key nobody vouched for, or a
signature that does not cover this account's key. That refusal is the whole of
the server's role in the trust chain.

The three primitives below are deliberately rebuilt here rather than imported
from ``workspace.vault.tests.reference``. That package is the parity oracle for
the browser bundle; a server importing it would stop being an independent
implementation, and the frozen vectors would prove nothing about it. The
duplication is guarded by ``FrozenVectorTests``, which replays the committed
vector through this module.
"""

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_URLSAFE_TO_STANDARD = str.maketrans("-_", "+/")

PUBKEY_ALG_X25519 = 0x01
PUBKEY_ALG_ED25519 = 0x02
SIG_ALG_ED25519 = 0x01

_PUBKEY_LENGTHS = {PUBKEY_ALG_X25519: 32, PUBKEY_ALG_ED25519: 32}
_ED25519_SIGNATURE_LENGTH = 64


class AttestationError(ValueError):
    """A submitted identity is malformed, or its attestation does not verify.

    One class for every reason on purpose: the caller answers with a single
    opaque refusal, so a client that is guessing learns nothing from which
    branch it hit.
    """


def decode_base64url(text) -> bytes:
    """Decode *text*, refusing anything that is not exactly base64url.

    ``validate=True`` matters: the default silently drops every character
    outside the alphabet, so ``"===="`` and ``"!!!!"`` both decode to no bytes
    at all and arrive downstream as an empty key or an empty signature rather
    than as a refusal.
    """
    if not isinstance(text, str) or not text:
        raise AttestationError("expected non-empty base64url text")
    try:
        raw = base64.b64decode(
            text.translate(_URLSAFE_TO_STANDARD) + "=" * (-len(text) % 4),
            validate=True,
        )
    except ValueError as exc:
        raise AttestationError("value is not valid base64url") from exc
    if not raw:
        raise AttestationError("value decodes to no bytes")
    return raw


def decode_public_key(stored: bytes, expected_alg: int) -> bytes:
    """Raw key bytes from the stored form, refusing the wrong algorithm.

    X25519 and Ed25519 keys are both 32 bytes, so the label is the only thing
    separating them once stored. Reading it and checking it is what keeps a
    signature key from reaching the KEM, or the reverse.
    """
    if not stored:
        raise AttestationError("public key is empty")
    if stored[0] != expected_alg:
        raise AttestationError(f"public key algorithm {stored[0]:#04x} is not expected")
    length = _PUBKEY_LENGTHS[expected_alg]
    if len(stored) != 1 + length:
        raise AttestationError("public key has the wrong length")
    return stored[1:]


def kex_pub_payload(account_uuid, kex_public_b64: str) -> bytes:
    """The exact bytes the client signed. ASCII, lowercase UUID, no newline.

    The account identifier is the AccountIdentity row's UUID, never a user id
    - see the catalogue note in the reference implementation for why.
    """
    return f"v1|account-kex-pub|{str(account_uuid).lower()}|{kex_public_b64}".encode(
        "ascii"
    )


def verify_kex_pub_attestation(
    account_uuid, kex_public_b64, sig_public_b64, sig_over_kex_pub_b64
) -> None:
    """Raise :class:`AttestationError` unless the attestation verifies."""
    if not isinstance(kex_public_b64, str) or not kex_public_b64:
        raise AttestationError("kex_public is missing")

    # Decoded and checked, though its bytes are not needed here: an unusable
    # key exchange key stored under a valid signature is an account that
    # onboards cleanly and can never be sealed to.
    decode_public_key(decode_base64url(kex_public_b64), PUBKEY_ALG_X25519)
    sig_public_raw = decode_public_key(
        decode_base64url(sig_public_b64), PUBKEY_ALG_ED25519
    )

    signature = decode_base64url(sig_over_kex_pub_b64)
    if signature[0] != SIG_ALG_ED25519:
        raise AttestationError("unsupported signature algorithm")
    if len(signature) != 1 + _ED25519_SIGNATURE_LENGTH:
        raise AttestationError("signature has the wrong length")

    try:
        Ed25519PublicKey.from_public_bytes(sig_public_raw).verify(
            signature[1:], kex_pub_payload(account_uuid, kex_public_b64)
        )
    except InvalidSignature as exc:
        raise AttestationError("attestation does not verify") from exc
