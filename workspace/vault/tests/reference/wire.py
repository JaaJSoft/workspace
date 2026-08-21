"""The six-byte ciphertext header.

HPKE-wrapped vault keys do NOT use this layout: they carry HPKE's own framed
output and their agility lives in VaultKeyWrap.hpke_suite.
"""

from dataclasses import dataclass

FORMAT_VERSION = 0x01

AEAD_AES_256_GCM = 0x01

KDF_DIRECT = 0x00
KDF_HKDF_SHA256 = 0x01

# iv_len is declared in the header rather than inferred, but it must agree with
# the AEAD: a mismatch is how a decoder gets tricked into slicing the ciphertext
# at the wrong offset.
IV_LENGTHS = {AEAD_AES_256_GCM: 12}

HEADER_LENGTH = 6


class UnsupportedVersion(ValueError):
    """Raised for a format_version this build cannot parse."""


@dataclass(frozen=True)
class WireCiphertext:
    format_version: int
    aead_id: int
    kdf_id: int
    key_version: int
    iv: bytes
    ciphertext: bytes


def encode_ciphertext(
    *, aead_id: int, kdf_id: int, key_version: int, iv: bytes, ciphertext: bytes
) -> bytes:
    if not 0 <= key_version <= 0xFFFF:
        raise ValueError(f"key_version {key_version} does not fit in two bytes")
    expected_iv = IV_LENGTHS.get(aead_id)
    if expected_iv is None:
        raise ValueError(f"unknown aead_id {aead_id:#04x}")
    if len(iv) != expected_iv:
        raise ValueError(
            f"iv is {len(iv)} bytes, aead {aead_id:#04x} wants {expected_iv}"
        )
    header = bytes(
        [FORMAT_VERSION, aead_id, kdf_id, key_version >> 8, key_version & 0xFF, len(iv)]
    )
    return header + iv + ciphertext


def decode_ciphertext(raw: bytes) -> WireCiphertext:
    if len(raw) < HEADER_LENGTH:
        raise ValueError("ciphertext shorter than its header")
    if raw[0] != FORMAT_VERSION:
        raise UnsupportedVersion(f"format_version {raw[0]:#04x}")
    aead_id, kdf_id = raw[1], raw[2]
    key_version = (raw[3] << 8) | raw[4]
    iv_len = raw[5]
    if IV_LENGTHS.get(aead_id) != iv_len:
        raise ValueError(f"iv_len {iv_len} is inconsistent with aead_id {aead_id:#04x}")
    # A truncated buffer would otherwise yield a short iv and an empty
    # ciphertext, and only fail later inside the AEAD.
    if len(raw) < HEADER_LENGTH + iv_len:
        raise ValueError("ciphertext shorter than its declared iv")
    return WireCiphertext(
        format_version=raw[0],
        aead_id=aead_id,
        kdf_id=kdf_id,
        key_version=key_version,
        iv=raw[HEADER_LENGTH : HEADER_LENGTH + iv_len],
        ciphertext=raw[HEADER_LENGTH + iv_len :],
    )
