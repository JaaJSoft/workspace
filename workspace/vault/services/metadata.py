"""Server-side verification of a vault's metadata signature.

The server opens nothing. What it can do is refuse to store metadata no other
client could ever verify - an unsigned vault, or one signed over values that
are not the ones about to be written.

The payload is rebuilt here from the columns being stored and re-encoded, and
the signature is checked against that. Verifying bytes the client chose would
only prove the client can sign its own sentence; verifying bytes the server
built proves the signature covers the row.
"""

import unicodedata

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .attestation import (
    PUBKEY_ALG_ED25519,
    SIG_ALG_ED25519,
    AttestationError,
    decode_base64url,
    decode_public_key,
)

VAULT_METADATA_TYPE = "vault-metadata"
_ED25519_SIGNATURE_LENGTH = 64


def vault_metadata_payload(
    *,
    vault_uuid,
    owner_account_uuid,
    encrypted_name,
    encrypted_description,
    icon,
    color,
    key_version,
    is_favorite,
) -> dict:
    """The exact key set the signature covers. Frozen: adding or removing one
    invalidates every signature already written."""
    return {
        "v": 1,
        "type": VAULT_METADATA_TYPE,
        "vault_uuid": str(vault_uuid).lower(),
        "owner_account_uuid": str(owner_account_uuid).lower(),
        "encrypted_name": encrypted_name,
        "encrypted_description": encrypted_description,
        "icon": icon,
        "color": color,
        "key_version": key_version,
        "is_favorite": is_favorite,
    }


def _signable(value):
    """Refuse anything the browser's encoder would not produce identically.

    bool before int, because bool is an int in Python and a True encoded as 1
    is a different byte from the one the browser writes. Floats have no
    canonical CBOR encoding at all.
    """
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        # The browser normalises before encoding, so a name typed with
        # combining accents on one keyboard signs the same bytes as the
        # precomposed spelling from another.
        return unicodedata.normalize("NFC", value)
    raise AttestationError(f"{type(value).__name__} cannot be signed")


def canonical_cbor(payload: dict) -> bytes:
    return cbor2.dumps(
        {_signable(key): _signable(item) for key, item in payload.items()},
        canonical=True,
    )


def verify_vault_metadata(payload, sig_public_b64, metadata_sig_b64) -> None:
    """Raise :class:`AttestationError` unless *metadata_sig_b64* signs the
    canonical encoding of *payload* under *sig_public_b64*."""
    sig_public_raw = decode_public_key(
        decode_base64url(sig_public_b64), PUBKEY_ALG_ED25519
    )
    signature = decode_base64url(metadata_sig_b64)
    if signature[0] != SIG_ALG_ED25519:
        raise AttestationError("unsupported signature algorithm")
    if len(signature) != 1 + _ED25519_SIGNATURE_LENGTH:
        raise AttestationError("signature has the wrong length")
    try:
        Ed25519PublicKey.from_public_bytes(sig_public_raw).verify(
            signature[1:], canonical_cbor(payload)
        )
    except InvalidSignature as exc:
        raise AttestationError("vault metadata signature does not verify") from exc
