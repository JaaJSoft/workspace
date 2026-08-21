"""Builds the cross-language test vectors.

Every input is fixed, never drawn: HPKE.seal picks an ephemeral key per call
and IV/salt draws are random, so "the same bytes" is only reachable when the
non-determinism is supplied as input. This is the single most likely reason a
vector file turns out to be unreproducible.

Regenerate with:
    uv run python -m workspace.vault.tests.reference.generate_vectors
"""

import json
import pathlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from . import ad, primitives
from .encoding import to_base64url

VECTORS_PATH = pathlib.Path(__file__).resolve().parent.parent / "crypto_vectors.json"

ENTRY_UUID = "0192f3a4-5b6c-7d8e-9f01-23456789abcd"
ACCOUNT_UUID = "0192f3a4-1111-7d8e-9f01-23456789abcd"
VAULT_UUID = "0192f3a4-2222-7d8e-9f01-23456789abcd"

# Fixed key material. These are test vectors, not secrets: readability beats
# entropy here, and a reviewer can recompute any line by hand.
SECRET_KEY = bytes(range(1, 33))
KDF_SALT = bytes(range(0xA0, 0xC0))
AEAD_KEY = bytes(range(32))
AEAD_IV = bytes(range(12))
SENDER_SK = bytes([0x11]) * 32
RECIPIENT_SK = bytes([0x22]) * 32
SIG_SK = bytes([0x33]) * 32


def build_vectors() -> dict:
    amk = primitives.derive_amk("Tr0ub4dor&3", SECRET_KEY, KDF_SALT)
    unwrap_key = primitives.hkdf(amk, ad.unwrap_info())

    sender = X25519PrivateKey.from_private_bytes(SENDER_SK)
    recipient = X25519PrivateKey.from_private_bytes(RECIPIENT_SK)
    signer = Ed25519PrivateKey.from_private_bytes(SIG_SK)

    hpke_info = ad.vault_key_info(VAULT_UUID, ACCOUNT_UUID)
    vault_key = bytes(range(0x40, 0x60))
    sealed = primitives.hpke_seal(
        recipient.public_key(), hpke_info, vault_key, sender_private=sender
    )

    # The stored form, prefix included: it is what the attestation signs.
    kex_pub_stored = primitives.encode_public_key(recipient.public_key())

    signed_payload = {
        "v": 1,
        "type": "entry_metadata",
        "uuid": ENTRY_UUID,
        "ts": 1755000000000,
    }

    return {
        "version": 1,
        "argon2id": [
            {
                "id": "amk-from-password-and-secret-key",
                "password": "Tr0ub4dor&3",
                "secret_key_b64": to_base64url(SECRET_KEY),
                "salt_b64": to_base64url(KDF_SALT),
                "params": primitives.ARGON2_PARAMS,
                "expected_amk_b64": to_base64url(amk),
            },
            {
                "id": "nfc-decomposed-password-derives-the-same-amk",
                # Decomposed on purpose (e + U+0301): the vector proves the
                # browser normalises before hashing, so the same password
                # typed on two keyboards opens the same vault.
                "password": "café",
                "secret_key_b64": to_base64url(SECRET_KEY),
                "salt_b64": to_base64url(KDF_SALT),
                "params": primitives.ARGON2_PARAMS,
                "expected_amk_b64": to_base64url(
                    primitives.derive_amk("café", SECRET_KEY, KDF_SALT)
                ),
            },
        ],
        "hkdf": [
            {
                "id": "unwrap-key",
                "ikm_b64": to_base64url(amk),
                "info": ad.unwrap_info().decode("ascii"),
                "expected_b64": to_base64url(unwrap_key),
            },
            {
                "id": "entry-key",
                "ikm_b64": to_base64url(vault_key),
                "info": ad.entry_key_info(ENTRY_UUID).decode("ascii"),
                "expected_b64": to_base64url(
                    primitives.hkdf(vault_key, ad.entry_key_info(ENTRY_UUID))
                ),
            },
        ],
        "aead": [
            {
                "id": "entry-field-password",
                "key_b64": to_base64url(AEAD_KEY),
                "iv_b64": to_base64url(AEAD_IV),
                "ad": ad.entry_field_ad(ENTRY_UUID, "password").decode("ascii"),
                "plaintext": "hunter2",
                "key_version": 1,
                "kdf_id": 0x01,
                "expected_wire_b64": to_base64url(
                    primitives.aead_seal(
                        AEAD_KEY,
                        b"hunter2",
                        ad.entry_field_ad(ENTRY_UUID, "password"),
                        iv=AEAD_IV,
                        key_version=1,
                        kdf_id=0x01,
                    )
                ),
            },
            {
                "id": "account-kex-priv-wrap",
                "key_b64": to_base64url(unwrap_key),
                "iv_b64": to_base64url(AEAD_IV),
                "ad": ad.kex_priv_ad(ACCOUNT_UUID).decode("ascii"),
                "plaintext": "private-key-bytes",
                "key_version": 0,
                "kdf_id": 0x00,
                "expected_wire_b64": to_base64url(
                    primitives.aead_seal(
                        unwrap_key,
                        b"private-key-bytes",
                        ad.kex_priv_ad(ACCOUNT_UUID),
                        iv=AEAD_IV,
                        key_version=0,
                        kdf_id=0x00,
                    )
                ),
            },
        ],
        "hpke": [
            {
                "id": "vault-key-self-wrap",
                "sender_sk_b64": to_base64url(SENDER_SK),
                "recipient_sk_b64": to_base64url(RECIPIENT_SK),
                "recipient_pk_b64": to_base64url(
                    primitives.public_bytes(recipient.public_key())
                ),
                "info": hpke_info.decode("ascii"),
                "plaintext_b64": to_base64url(vault_key),
                "expected_sealed_b64": to_base64url(sealed),
            }
        ],
        "cbor": [
            {
                "id": "entry-metadata",
                "payload": signed_payload,
                "expected_b64": to_base64url(primitives.canonical_cbor(signed_payload)),
            },
            {
                "id": "short-key-sorts-before-long-key",
                "payload": {"bb": 1, "a": 2},
                "expected_b64": to_base64url(
                    primitives.canonical_cbor({"bb": 1, "a": 2})
                ),
            },
            {
                # Both keys encode to three bytes, so their UTF-8 content
                # decides: 0x7a beats 0xc3 and "zz" comes first. An
                # implementation sorting on the JavaScript string length gets
                # this backwards, which is why it is frozen here.
                "id": "non-ascii-key-sorts-by-its-utf8-bytes",
                "payload": {"é": 1, "zz": 2},
                "expected_b64": to_base64url(
                    primitives.canonical_cbor({"é": 1, "zz": 2})
                ),
            },
            {
                # The value is decomposed (e + U+0301). Canonical CBOR
                # normalises to NFC before encoding, so this must produce the
                # same bytes as the precomposed spelling - otherwise one entry
                # name signs two ways depending on the keyboard it was typed on.
                "id": "nfc-normalises-a-decomposed-string-value",
                "payload": {"v": 1, "type": "test", "name": "café"},
                "expected_b64": to_base64url(
                    primitives.canonical_cbor({"v": 1, "type": "test", "name": "café"})
                ),
            },
        ],
        "ed25519": [
            {
                "id": "entry-metadata-signature",
                "sk_b64": to_base64url(SIG_SK),
                "pk_b64": to_base64url(primitives.public_bytes(signer.public_key())),
                "payload": signed_payload,
                "expected_sig_b64": to_base64url(
                    primitives.sign(signer, signed_payload)
                ),
            },
            {
                # The account key attestation signs the catalogue string
                # itself, not a CBOR payload. Signing it through the CBOR path
                # would wrap it in a byte string and verify nowhere.
                "id": "account-kex-pub-attestation",
                "sk_b64": to_base64url(SIG_SK),
                "pk_b64": to_base64url(primitives.public_bytes(signer.public_key())),
                # Both halves are published so each language rebuilds the
                # payload instead of replaying an opaque string.
                "account_uuid": ACCOUNT_UUID,
                "kex_public_b64": to_base64url(kex_pub_stored),
                "message_b64": to_base64url(
                    ad.kex_pub_payload(ACCOUNT_UUID, to_base64url(kex_pub_stored))
                ),
                "expected_sig_b64": to_base64url(
                    primitives.sign_bytes(
                        signer,
                        ad.kex_pub_payload(ACCOUNT_UUID, to_base64url(kex_pub_stored)),
                    )
                ),
            },
        ],
        "public_keys": [
            {
                # X25519 and Ed25519 are both 32 raw bytes; only the label
                # separates them once stored, so the label is what is frozen.
                "id": "x25519-public-key-encoding",
                "raw_b64": to_base64url(
                    primitives.public_bytes(recipient.public_key())
                ),
                "alg": primitives.PUBKEY_ALG_X25519,
                "expected_stored_b64": to_base64url(kex_pub_stored),
            },
            {
                "id": "ed25519-public-key-encoding",
                "raw_b64": to_base64url(primitives.public_bytes(signer.public_key())),
                "alg": primitives.PUBKEY_ALG_ED25519,
                "expected_stored_b64": to_base64url(
                    primitives.encode_public_key(
                        signer.public_key(), primitives.PUBKEY_ALG_ED25519
                    )
                ),
            },
        ],
    }


def main() -> None:
    VECTORS_PATH.write_text(
        json.dumps(build_vectors(), indent=2, ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {VECTORS_PATH}")


if __name__ == "__main__":
    main()
