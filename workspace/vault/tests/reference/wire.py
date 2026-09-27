"""The six-byte ciphertext header.

HPKE-wrapped vault keys do NOT use this layout: they carry HPKE's own framed
output and their agility lives in VaultKeyWrap.hpke_suite.

Which formats and AEADs exist, and each AEAD's nonce length, come from the
manifest.
"""

from dataclasses import dataclass

from .suites import CURRENT_SUITE, entry

AEAD_AES_256_GCM = 0x01
AEAD_TEST_CTR_HMAC = 0xF0

KDF_DIRECT = 0x00
KDF_HKDF_SHA256 = 0x01

HEADER_LENGTH = 6


@dataclass(frozen=True)
class WireCiphertext:
    format_version: int
    aead_id: int
    kdf_id: int
    key_version: int
    iv: bytes
    ciphertext: bytes
    header: bytes


def encode_ciphertext(
    *,
    format_version: int | None = None,
    aead_id: int,
    kdf_id: int,
    key_version: int,
    iv: bytes,
    ciphertext: bytes,
) -> bytes:
    if format_version is None:
        format_version = CURRENT_SUITE["format_version"]
    entry("format", format_version)
    if not 0 <= key_version <= 0xFFFF:
        raise ValueError(f"key_version {key_version} does not fit in two bytes")
    expected_iv = entry("aead", aead_id)["iv_length"]
    if len(iv) != expected_iv:
        raise ValueError(
            f"iv is {len(iv)} bytes, aead {aead_id:#04x} wants {expected_iv}"
        )
    header = bytes(
        [format_version, aead_id, kdf_id, key_version >> 8, key_version & 0xFF, len(iv)]
    )
    return header + iv + ciphertext


def decode_ciphertext(raw: bytes) -> WireCiphertext:
    if len(raw) < HEADER_LENGTH:
        raise ValueError("ciphertext shorter than its header")
    # Format first, before any other byte is read: a future layout must never
    # be half-parsed by an old reader.
    entry("format", raw[0])
    aead_id, kdf_id = raw[1], raw[2]
    iv_len = raw[5]
    if entry("aead", aead_id)["iv_length"] != iv_len:
        raise ValueError(f"iv_len {iv_len} is inconsistent with aead_id {aead_id:#04x}")
    # A truncated buffer would otherwise yield a short iv and an empty
    # ciphertext, and only fail later inside the AEAD.
    if len(raw) < HEADER_LENGTH + iv_len:
        raise ValueError("ciphertext shorter than its declared iv")
    return WireCiphertext(
        format_version=raw[0],
        aead_id=aead_id,
        kdf_id=kdf_id,
        key_version=(raw[3] << 8) | raw[4],
        iv=raw[HEADER_LENGTH : HEADER_LENGTH + iv_len],
        ciphertext=raw[HEADER_LENGTH + iv_len :],
        header=raw[:HEADER_LENGTH],
    )
