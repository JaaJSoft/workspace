"""Reading an export archive, in Python, from the format alone.

Test-only, like the rest of this package, and deliberately written from the
spec's byte offsets rather than from a helper the writer also uses: the point
is to prove the file opens for something that has never run the browser's code.

There is no archive reader in production - import is v2 - so this file is what
the round-trip test opens with.
"""

import unicodedata

import cbor2

from . import primitives

MAGIC = b"VLTARCH"
CONTAINER_VERSION = 1
KDF_ARGON2ID = 1
HEADER_LENGTH = 50
SALT_OFFSET = 18
ARCHIVE_KEY_INFO = b"v1|archive-key"

# Verbatim from the spec. Widening later accepts more archives; narrowing would
# reject one already written.
BOUNDS = {"m": (8192, 1048576), "t": (1, 10), "p": (1, 4)}


class ArchiveError(Exception):
    """Not an archive, an archive we cannot read, or one that was altered."""


def read_header(blob: bytes) -> dict:
    """Everything a reader has before it can derive anything."""
    if len(blob) < HEADER_LENGTH:
        raise ArchiveError("not a vault archive")
    if blob[:7] != MAGIC:
        raise ArchiveError("not a vault archive")
    if blob[7] != CONTAINER_VERSION:
        raise ArchiveError(f"unsupported archive version {blob[7]}")
    if blob[8] != KDF_ARGON2ID:
        raise ArchiveError(f"unsupported archive kdf {blob[8]}")
    params = {
        "m": int.from_bytes(blob[9:13], "big"),
        "t": int.from_bytes(blob[13:17], "big"),
        "p": blob[17],
    }
    for name, value in params.items():
        low, high = BOUNDS[name]
        if not low <= value <= high:
            raise ArchiveError(f"archive {name} is {value}, outside [{low}, {high}]")
    return {
        "header": blob[:HEADER_LENGTH],
        "params": params,
        "salt": blob[SALT_OFFSET:HEADER_LENGTH],
        "payload": blob[HEADER_LENGTH:],
    }


def derive_archive_key(passphrase: str, salt: bytes, params: dict) -> bytes:
    """Argon2id with no K - an export has no secret_key - then HKDF."""
    ikm = primitives.argon2id_raw(
        password=unicodedata.normalize("NFC", passphrase).encode("utf-8"),
        salt=salt,
        secret=b"",
        associated_data=b"",
        t=params["t"],
        m=params["m"],
        p=params["p"],
        tag_length=32,
    )
    return primitives.hkdf(ikm, ARCHIVE_KEY_INFO, 32)


def open_archive(blob: bytes, passphrase: str) -> dict:
    parts = read_header(blob)
    key = derive_archive_key(passphrase, parts["salt"], parts["params"])
    # The whole public header is the associated data, so a flipped byte fails
    # here as tampering rather than surfacing as a wrong passphrase.
    plaintext = primitives.aead_open(key, parts["payload"], parts["header"])
    return cbor2.loads(plaintext)
