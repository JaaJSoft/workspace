"""The crypto suite manifest, as the server reads it.

Data only: which ids exist and in which state. The server keeps its own
implementations (attestation.py, metadata.py) so that it stays an independent
check on the browser, and it never serves this file - a client that fetched
its accepted algorithms from the server would let the server choose them.
"""

import json
from functools import cache
from pathlib import Path

from django.conf import settings

MANIFEST_PATH = Path(__file__).resolve().parent.parent / "crypto_suites.json"
# format, aead, kdf, key version (two bytes), nonce length.
_HEADER_LENGTH = 6
_HPKE_FIELDS = ("kem_id", "kdf_id", "aead_id", "mode")


class UnsupportedCiphertext(ValueError):
    """A ciphertext whose header this server will not store."""


@cache
def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _entry(axis: str, identifier):
    found = _manifest()[axis].get(str(identifier))
    if found is None:
        return None
    if found["state"] == "test" and not settings.VAULT_TEST_SUITES:
        return None
    return found


def _is_integer(value) -> bool:
    # bool is an int in Python, and True == 1: it must not stand in for an id.
    return isinstance(value, int) and not isinstance(value, bool)


def readable(axis: str, identifier) -> bool:
    return _entry(axis, identifier) is not None


def check_ciphertext(raw: bytes) -> None:
    if len(raw) < _HEADER_LENGTH:
        raise UnsupportedCiphertext("ciphertext shorter than its header")
    if not readable("format", raw[0]):
        raise UnsupportedCiphertext("unsupported format")
    aead = _entry("aead", raw[1])
    if aead is None:
        raise UnsupportedCiphertext("unsupported aead")
    if raw[5] != aead["iv_length"] or len(raw) < _HEADER_LENGTH + raw[5]:
        raise UnsupportedCiphertext("nonce length does not match the aead")


def check_hpke_suite(value) -> None:
    """Format 1 is spelled by the key's absence: an explicit 1 would pair a
    format-1 suite with an info naming its suite, which no writer ever sealed
    under. The ids must be integers equal to the declared ones - True == 1 and
    2.0 == 2 in Python, so equality alone would let either stand in."""
    if not isinstance(value, dict):
        raise ValueError("hpke_suite must be an object")
    if "format" in value:
        fmt = value["format"]
        if not _is_integer(fmt) or fmt == 1:
            raise ValueError("unsupported HPKE suite")
    else:
        fmt = 1
    declared = _entry("hpke", fmt)
    ids = {key: item for key, item in value.items() if key != "format"}
    if (
        declared is None
        or ids.keys() != set(_HPKE_FIELDS)
        or any(
            not _is_integer(item) or item != declared["suite"][key]
            for key, item in ids.items()
        )
    ):
        raise ValueError("unsupported HPKE suite")


def check_kdf_algo(algo) -> dict:
    declared = _entry("kdf", algo)
    if declared is None:
        raise ValueError("unsupported kdf_algo")
    return declared


def check_account_kdf(algo, params) -> None:
    declared = check_kdf_algo(algo)
    if not isinstance(params, dict) or params.get("v") != declared["params"]["v"]:
        raise ValueError("unsupported kdf_params")
    for name, (low, high) in declared["bounds"].items():
        value = params.get(name)
        if not _is_integer(value) or not low <= value <= high:
            raise ValueError(f"kdf_params.{name} is outside the supported range")


def pubkey_length(alg_id: int) -> int | None:
    found = _entry("pubkey", alg_id)
    return None if found is None else found["length"]


def signature_length(alg_id: int) -> int | None:
    found = _entry("signature", alg_id)
    return None if found is None else found["length"]
