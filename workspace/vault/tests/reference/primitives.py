"""Reference implementation of the vault primitives, in Python.

Test-only. It exists to generate crypto_vectors.json and to prove the browser
bundle produces the same bytes; application code must never import it.

Every primitive and every constant here is normative. Where a library offers a
convenient high-level call that drops a parameter this hierarchy depends on,
the low-level API is used instead and the comment says why.
"""

import unicodedata

import cbor2
from argon2.low_level import Type, core, ffi, lib
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pyhpke import AEADId, CipherSuite, KDFId, KEMId
from pyhpke.kem_key import KEMKeyPair

from . import wire

ARGON2_PARAMS = {"algo": "argon2id", "v": "1.3", "m": 65536, "t": 3, "p": 2}

# One byte in front of every persisted signature and public key, so a future
# algorithm lands without a data migration.
SIG_ALG_ED25519 = 0x01
PUBKEY_ALG_X25519 = 0x01
# Ed25519 carries its own label even though both keys are 32 raw bytes: under
# one shared label the two would be indistinguishable once stored. Reading the
# label back is not this decoder's job - the attestation signs the labelled
# form, so a swap breaks the signature the client re-checks against the signing
# key it unwrapped. The server, which only ever sees a pair the client signed
# itself, pins the expected algorithm instead.
PUBKEY_ALG_ED25519 = 0x02

# Raw key length per algorithm: a stored key of the wrong size is refused
# rather than truncated.
_PUBKEY_LENGTHS = {PUBKEY_ALG_X25519: 32, PUBKEY_ALG_ED25519: 32}

SECRET_KEY_LENGTH = 32
SALT_LENGTH = 32

HPKE_SUITE_V1 = {"kem_id": 0x0020, "kdf_id": 0x0001, "aead_id": 0x0002, "mode": 0x00}

# HKDF salt is 32 zero bytes rather than drawn: the input keying material is
# already a uniformly random key, so a salt buys nothing and a drawn one would
# have to be stored and shipped with every derivation.
_HKDF_SALT = bytes(32)


def argon2id_raw(
    *,
    password: bytes,
    salt: bytes,
    secret: bytes,
    associated_data: bytes,
    t: int,
    m: int,
    p: int,
    tag_length: int,
) -> bytes:
    """Argon2id over the raw argon2_context struct.

    core() takes the struct rather than keyword arguments, which is precisely
    why it is used: it is the only argon2-cffi entry point exposing the
    `secret` field. hash_secret_raw() has no K parameter at all - its own
    `secret` argument is the password - so calling it would derive from the
    password alone and silently halve the security budget.

    Every field is a parameter here so the published test vectors, which use a
    non-empty associated data and their own cost parameters, reach exactly the
    plumbing the vault derivation uses.
    """
    out = ffi.new("uint8_t[]", tag_length)
    context = ffi.new(
        "argon2_context *",
        {
            "version": 0x13,
            "out": out,
            "outlen": tag_length,
            "pwd": ffi.new("uint8_t[]", password),
            "pwdlen": len(password),
            "salt": ffi.new("uint8_t[]", salt),
            "saltlen": len(salt),
            "secret": ffi.new("uint8_t[]", secret),
            "secretlen": len(secret),
            "ad": ffi.new("uint8_t[]", associated_data)
            if associated_data
            else ffi.NULL,
            "adlen": len(associated_data),
            "t_cost": t,
            "m_cost": m,
            "lanes": p,
            "threads": p,
            "allocate_cbk": ffi.NULL,
            "free_cbk": ffi.NULL,
            "flags": lib.ARGON2_DEFAULT_FLAGS,
        },
    )
    rc = core(context, Type.ID.value)
    if rc != lib.ARGON2_OK:
        raise ValueError(ffi.string(lib.argon2_error_message(rc)).decode("ascii"))
    return bytes(ffi.buffer(out, tag_length))


def derive_amk(password: str, secret_key: bytes, salt: bytes, params=None) -> bytes:
    """Argon2id with secret_key passed as K, never concatenated.

    Argon2 accepts a K and a salt of any length, so a secret_key one character
    short derives a different AMK instead of failing, and only surfaces later
    as a GCM tag error the UI can report as nothing but a wrong password. The
    guard belongs here and not in argon2id_raw, which the published RFC 9106
    vectors reach with their own lengths.
    """
    for name, value, expected in (
        ("secret_key", secret_key, SECRET_KEY_LENGTH),
        ("salt", salt, SALT_LENGTH),
    ):
        if len(value) != expected:
            raise ValueError(f"{name} is {len(value)} bytes, expected {expected}")
    params = params or ARGON2_PARAMS
    # NFC applies to the KDF input, not just to the length check: "café"
    # precomposed and decomposed are different byte strings otherwise.
    password_input = unicodedata.normalize("NFC", password).encode("utf-8")
    return argon2id_raw(
        password=password_input,
        salt=salt,
        secret=secret_key,
        associated_data=b"",  # empty, normative
        t=params["t"],
        m=params["m"],
        p=params["p"],
        tag_length=32,
    )


def hkdf_with_salt(ikm: bytes, salt: bytes, info: bytes, length: int) -> bytes:
    """HKDF-SHA256 with the salt exposed, so published vectors can reach it."""
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(
        ikm
    )


def hkdf(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    return hkdf_with_salt(ikm, _HKDF_SALT, info, length)


AEAD_KEY_LENGTH = 32


def aead_seal(
    key: bytes,
    plaintext: bytes,
    associated_data: bytes,
    *,
    iv: bytes,
    key_version: int,
    kdf_id: int,
) -> bytes:
    # AESGCM infers the variant from the key length, so a 16-byte key would
    # quietly produce AES-128-GCM under a header still declaring AES-256-GCM.
    if len(key) != AEAD_KEY_LENGTH:
        raise ValueError(
            f"aes-256-gcm needs a {AEAD_KEY_LENGTH}-byte key, got {len(key)}"
        )
    ciphertext = AESGCM(key).encrypt(iv, plaintext, associated_data)
    return wire.encode_ciphertext(
        aead_id=wire.AEAD_AES_256_GCM,
        kdf_id=kdf_id,
        key_version=key_version,
        iv=iv,
        ciphertext=ciphertext,
    )


def aead_open(key: bytes, raw: bytes, associated_data: bytes) -> bytes:
    if len(key) != AEAD_KEY_LENGTH:
        raise ValueError(
            f"aes-256-gcm needs a {AEAD_KEY_LENGTH}-byte key, got {len(key)}"
        )
    decoded = wire.decode_ciphertext(raw)
    # An open failure is surfaced as-is. Never retried with another AD, never
    # returned as partial plaintext. Retrying turns the AD into an oracle.
    return AESGCM(key).decrypt(decoded.iv, decoded.ciphertext, associated_data)


def generate_kex_keypair() -> X25519PrivateKey:
    return X25519PrivateKey.generate()


def generate_sig_keypair() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def public_bytes(key) -> bytes:
    return key.public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )


def encode_public_key(key, alg: int = PUBKEY_ALG_X25519) -> bytes:
    """The stored form of a public key: algorithm byte, then the raw key.

    The attestation signs this form, not the bare key: an unsigned algorithm
    byte would be the server's to change while the signature still verifies.
    """
    expected = _PUBKEY_LENGTHS.get(alg)
    if expected is None:
        raise ValueError(f"unknown public key algorithm {alg:#04x}")
    raw = public_bytes(key)
    if len(raw) != expected:
        raise ValueError(
            f"public key is {len(raw)} bytes, algorithm {alg:#04x} wants {expected}"
        )
    return bytes([alg]) + raw


def decode_public_key(stored: bytes) -> bytes:
    """Raw key bytes from the stored form.

    The KEM never sees the prefix: DHKEM(X25519) deserializes a bare 32-byte
    key, so handing it the stored form would read the label as key material.
    """
    if not stored:
        raise ValueError("public key is empty")
    expected = _PUBKEY_LENGTHS.get(stored[0])
    if expected is None:
        raise ValueError(f"unsupported public key algorithm {stored[0]:#04x}")
    if len(stored) != 1 + expected:
        raise ValueError(
            f"public key is {len(stored) - 1} bytes, "
            f"algorithm {stored[0]:#04x} wants {expected}"
        )
    return stored[1:]


def private_bytes(key) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )


def hpke_suite(
    kem: KEMId = KEMId.DHKEM_X25519_HKDF_SHA256,
    kdf: KDFId = KDFId.HKDF_SHA256,
    aead: AEADId = AEADId.AES256_GCM,
) -> CipherSuite:
    """Suite v1 by default; the arguments exist for the published vectors,
    which cover this KEM and KDF only in their AES-128-GCM variant.
    """
    return CipherSuite.new(kem, kdf, aead)


def hpke_seal(
    recipient_public: X25519PublicKey,
    info: bytes,
    plaintext: bytes,
    *,
    sender_private: X25519PrivateKey,
) -> bytes:
    """mode_base seal, aad empty - all context binding lives in info.

    sender_private is required rather than generated: HPKE draws an ephemeral
    key per call, so a vector could not be reproduced without pinning it. It is
    passed as `eks`, the ephemeral key pair, and never as `sks` - `sks` switches
    the suite to mode_auth, which binds the wrap to a sender identity nothing
    here verifies.
    """
    suite = hpke_suite()
    pk = suite.kem.deserialize_public_key(public_bytes(recipient_public))
    ephemeral = KEMKeyPair(
        suite.kem.deserialize_private_key(private_bytes(sender_private)),
        suite.kem.deserialize_public_key(public_bytes(sender_private.public_key())),
    )
    enc, sender = suite.create_sender_context(pkr=pk, info=info, eks=ephemeral)
    return enc + sender.seal(plaintext, aad=b"")


def hpke_open(recipient_private: X25519PrivateKey, info: bytes, sealed: bytes) -> bytes:
    suite = hpke_suite()
    enc_length = 32  # DHKEM(X25519) encapsulated key size
    enc, ciphertext = sealed[:enc_length], sealed[enc_length:]
    sk = suite.kem.deserialize_private_key(private_bytes(recipient_private))
    recipient = suite.create_recipient_context(enc=enc, skr=sk, info=info)
    return recipient.open(ciphertext, aad=b"")


# Negative integers in this band encode canonically as a four-byte argument,
# but the browser bundle cannot produce that form: cbor-x sends anything below
# the JavaScript int32 range through its BigInt path, which always writes eight
# bytes. Rather than let the two implementations sign different bytes for the
# same number, both refuse the band outright - no vault payload has a use for
# it, and a value that cannot be encoded identically everywhere is not one this
# format can carry.
_UNENCODABLE_NEGATIVE_RANGE = (-(2**32), -(2**31) - 1)


def _canonicalise(value):
    """Reject what cannot be encoded unambiguously, NFC-normalise the rest.

    Two spellings of the same accented text are different byte strings and
    would produce different signatures over what a user sees as one value, so
    normalisation is part of the encoding rather than the caller's problem.

    The accepted types are closed on purpose, and match the browser's exactly:
    anything else - a set, a datetime, an arbitrary object - has no encoding
    both implementations agree on, and guessing one means signing bytes the
    other would never produce.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float):
        raise ValueError("floats are forbidden in canonical CBOR")
    if isinstance(value, int):
        low, high = _UNENCODABLE_NEGATIVE_RANGE
        if low <= value <= high:
            raise ValueError(
                f"integer {value} has no encoding both implementations agree on"
            )
        return value
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"canonical CBOR map keys must be strings, got {type(key).__name__}"
                )
            normalised = _canonicalise(key)
            if normalised in out:
                # The two keys are one key once normalised, and each
                # implementation would keep a different one of the two values.
                raise ValueError(f"map keys collide after NFC normalisation: {key!r}")
            out[normalised] = _canonicalise(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_canonicalise(item) for item in value]
    raise ValueError(f"unsupported type in canonical CBOR: {type(value).__name__}")


def canonical_cbor(payload) -> bytes:
    """Deterministic CBOR: sorted keys, definite lengths, no tags, no floats."""
    return cbor2.dumps(_canonicalise(payload), canonical=True)


def sign_bytes(private_key: Ed25519PrivateKey, message: bytes) -> bytes:
    """Ed25519 over raw bytes, carrying the algorithm prefix.

    Not everything signed is a CBOR payload: the account key attestation signs
    a plain ASCII string built by the associated-data catalogue. Routing it
    through sign() would wrap it in a CBOR byte string and produce a signature
    no conforming verifier accepts.
    """
    return bytes([SIG_ALG_ED25519]) + private_key.sign(message)


def verify_bytes(
    public_key: Ed25519PublicKey, message: bytes, signature: bytes
) -> None:
    if signature[0] != SIG_ALG_ED25519:
        raise ValueError(f"unsupported signature algorithm {signature[0]:#04x}")
    public_key.verify(signature[1:], message)


def sign(private_key: Ed25519PrivateKey, payload) -> bytes:
    """Sign the canonical CBOR of *payload*.

    Ed25519 pure, empty context: domain separation lives in the payload's
    `type` field, not in a signature context parameter.
    """
    return sign_bytes(private_key, canonical_cbor(payload))


def verify(
    public_key: Ed25519PublicKey,
    payload_bytes: bytes,
    signature: bytes,
    expected_type: str,
) -> dict:
    """Decode, check version, check type, re-canonicalise, verify Ed25519.

    Any step may reject, and the order is the point: the type check runs before
    any signature maths, so a payload replayed as another kind never reaches
    Ed25519 at all.
    """
    payload = cbor2.loads(payload_bytes)  # 1. decode
    if payload.get("v") != 1:  # 2. version
        raise ValueError(f"unsupported payload version {payload.get('v')!r}")
    if payload.get("type") != expected_type:  # 3. type, before any crypto
        raise ValueError(
            f"payload type {payload.get('type')!r} does not match {expected_type!r}"
        )
    if canonical_cbor(payload) != payload_bytes:  # 4. re-canonicalise
        raise ValueError("payload is not canonically encoded")
    verify_bytes(public_key, payload_bytes, signature)  # 5. Ed25519
    return payload


# Crockford base32: the alphabet drops I, L, O and U so a hand-transcribed
# secret cannot be lost to the 0/O and 1/I confusion, and the check symbol
# extends it with five more characters that never appear in a payload.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CROCKFORD_CHECK = _CROCKFORD + "*~$=U"
_CROCKFORD_DECODE = {c: i for i, c in enumerate(_CROCKFORD)}
_CROCKFORD_DECODE.update({"O": 0, "I": 1, "L": 1})


def crockford_encode(raw: bytes) -> str:
    """The stored form of a recovery secret: base32 plus one check symbol.

    The check symbol is the payload read as one big integer, modulo 37 - the
    scheme Crockford defines, which catches any single wrong character and any
    transposition of two neighbours. Without it a mistyped secret is
    indistinguishable from a wrong password at unlock time.
    """
    value = int.from_bytes(raw, "big")
    width = (len(raw) * 8 + 4) // 5
    symbols = [
        _CROCKFORD[(value >> (shift * 5)) & 0x1F] for shift in range(width - 1, -1, -1)
    ]
    return "".join(symbols) + _CROCKFORD_CHECK[value % 37]


def crockford_decode(text: str) -> bytes:
    """Raw bytes from the stored form, refusing anything the check rejects.

    Hyphens are grouping, not data: the kit prints the secret in blocks so it
    can be copied by hand, and a user retyping it keeps them.
    """
    cleaned = text.replace("-", "").replace(" ", "").upper()
    if len(cleaned) < 2:
        raise ValueError("recovery secret is too short")
    body, check = cleaned[:-1], cleaned[-1]
    value = 0
    for symbol in body:
        digit = _CROCKFORD_DECODE.get(symbol)
        if digit is None:
            raise ValueError(f"illegal character {symbol!r} in recovery secret")
        value = (value << 5) | digit
    if check not in _CROCKFORD_CHECK:
        raise ValueError("illegal check symbol")
    if _CROCKFORD_CHECK.index(check) != value % 37:
        raise ValueError("recovery secret fails its check symbol")
    length = len(body) * 5 // 8
    if not length:
        return b""
    return value.to_bytes(length + 1, "big")[-length:]
