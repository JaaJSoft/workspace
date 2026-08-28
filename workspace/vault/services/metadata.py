"""Server-side verification of the signature on a stored record.

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
ENTRY_METADATA_TYPE = "entry-metadata"
FOLDER_METADATA_TYPE = "folder-metadata"
TAG_METADATA_TYPE = "tag-metadata"
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


def entry_metadata_payload(
    *,
    entry_uuid,
    vault_uuid,
    signer_account_uuid,
    entry_type,
    folder_uuid,
    encrypted_name,
    encrypted_notes,
    key_version,
    entry_version,
    is_favorite,
    tag_uuids,
    fields,
) -> dict:
    """The exact key set an entry's signature covers.

    ``tags`` and ``fields`` are in it because associated data cannot see a
    field that was deleted, a ciphertext replayed into its own slot, or a tag
    detached: it only stops one from moving somewhere it does not belong. Both
    are sorted, because a signature over the order rows happened to come back
    in would verify differently on the next read.
    """
    return {
        "v": 1,
        "type": ENTRY_METADATA_TYPE,
        "entry_uuid": str(entry_uuid).lower(),
        "vault_uuid": str(vault_uuid).lower(),
        "signer_account_uuid": str(signer_account_uuid).lower(),
        "entry_type": entry_type,
        "folder_uuid": str(folder_uuid).lower() if folder_uuid else None,
        "encrypted_name": encrypted_name,
        "encrypted_notes": encrypted_notes,
        "key_version": key_version,
        "entry_version": entry_version,
        "is_favorite": is_favorite,
        "tags": sorted(str(value).lower() for value in tag_uuids),
        "fields": [[key, fields[key]] for key in sorted(fields)],
    }


def folder_metadata_payload(
    *,
    folder_uuid,
    vault_uuid,
    signer_account_uuid,
    parent_uuid,
    position,
    encrypted_name,
) -> dict:
    """``parent_uuid`` and ``position`` are the reason this payload exists:
    they are plaintext, and nothing else covers them."""
    return {
        "v": 1,
        "type": FOLDER_METADATA_TYPE,
        "folder_uuid": str(folder_uuid).lower(),
        "vault_uuid": str(vault_uuid).lower(),
        "signer_account_uuid": str(signer_account_uuid).lower(),
        "parent_uuid": str(parent_uuid).lower() if parent_uuid else None,
        "position": position,
        "encrypted_name": encrypted_name,
    }


def tag_metadata_payload(
    *,
    tag_uuid,
    vault_uuid,
    signer_account_uuid,
    encrypted_name,
    color,
) -> dict:
    return {
        "v": 1,
        "type": TAG_METADATA_TYPE,
        "tag_uuid": str(tag_uuid).lower(),
        "vault_uuid": str(vault_uuid).lower(),
        "signer_account_uuid": str(signer_account_uuid).lower(),
        "encrypted_name": encrypted_name,
        "color": color,
    }


def _signable(value):
    """Refuse anything the browser's encoder would not produce identically.

    bool before int, because bool is an int in Python and a True encoded as 1
    is a different byte from the one the browser writes. Floats have no
    canonical CBOR encoding at all. Recursive, because a payload member may be
    a list of pairs and the browser normalises all the way down - a combining
    accent nested two levels deep would otherwise sign different bytes on each
    side, and nothing would report it but a signature that stops verifying.
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
    # A tuple normalises onto a list: cbor2 encodes both as a CBOR array and
    # the browser only ever produces arrays, so the two must not stay
    # distinguishable here.
    if isinstance(value, (list, tuple)):
        return [_signable(item) for item in value]
    if isinstance(value, dict):
        return {_signable(key): _signable(item) for key, item in value.items()}
    raise AttestationError(f"{type(value).__name__} cannot be signed")


def canonical_cbor(payload: dict) -> bytes:
    return cbor2.dumps(_signable(payload), canonical=True)


def verify_record(payload, sig_public_b64, metadata_sig_b64) -> None:
    """Raise :class:`AttestationError` unless *metadata_sig_b64* signs the
    canonical encoding of *payload* under *sig_public_b64*.

    The payload is always one this module built from the columns about to be
    written, never one a client sent: verifying bytes the client chose would
    only prove the client can sign its own sentence.
    """
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
        raise AttestationError("record signature does not verify") from exc


# The name views/vaults.py calls. Kept because a vault's signature is verified
# no differently from any other record's.
verify_vault_metadata = verify_record
