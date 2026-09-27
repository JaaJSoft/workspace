"""The crypto suite manifest, as the reference implementation reads it.

The manifest is shared with the browser bundle and the server so that the
three agree on which algorithm ids exist and which one is written. What each
id *does* stays in each program's own code: the reference must remain an
independent implementation, or the vectors would only prove it agrees with
itself.

The reference is test-only, so it reads every state - "test" included.
"""

import json
from functools import cache
from pathlib import Path

MANIFEST_PATH = Path(__file__).resolve().parents[2] / "crypto_suites.json"

_READABLE = frozenset({"current", "superseded", "test"})


class UnsupportedAlgorithm(ValueError):
    """The stored bytes name an algorithm this build does not read.

    Raised on public bytes only, before any cryptographic operation, so it
    says nothing about the key or the password.
    """

    def __init__(self, axis: str, identifier):
        super().__init__(f"unsupported {axis} {identifier!r}")
        self.axis = axis
        self.identifier = identifier


@cache
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def entry(axis: str, identifier) -> dict:
    found = manifest()[axis].get(str(identifier))
    if found is None or found["state"] not in _READABLE:
        raise UnsupportedAlgorithm(axis, identifier)
    return found


def _is_integer(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def stored_hpke_format(stored) -> int:
    """The format a stored hpke_suite names, under the one rule every reader
    of it applies - the opener and the info builder alike.

    Format 1 is spelled by the key's absence: an explicit 1 would pair a
    format-1 suite with an info naming its suite, which no writer ever sealed
    under. The ids must be integers equal to the declared ones - True == 1 and
    2.0 == 2 in Python, so equality alone would let either stand in.
    """
    if not isinstance(stored, dict):
        raise UnsupportedAlgorithm("hpke", stored)
    fmt = stored.get("format", 1)
    if "format" in stored and (not _is_integer(fmt) or fmt == 1):
        raise UnsupportedAlgorithm("hpke", stored)
    declared = entry("hpke", fmt)["suite"]
    ids = {key: value for key, value in stored.items() if key != "format"}
    if ids.keys() != declared.keys() or any(
        not _is_integer(value) or value != declared[key] for key, value in ids.items()
    ):
        raise UnsupportedAlgorithm("hpke", stored)
    return fmt


def _current(axis: str, **match) -> str:
    keys = [
        key
        for key, value in manifest()[axis].items()
        if value["state"] == "current"
        and all(value.get(field) == wanted for field, wanted in match.items())
    ]
    if len(keys) != 1:
        raise RuntimeError(f"manifest axis {axis} {match} has {len(keys)} current entries")
    return keys[0]


def _current_suite() -> dict:
    hpke_format = _current("hpke")
    kdf_algo = _current("kdf")
    return {
        "format_version": int(_current("format")),
        "aead_id": int(_current("aead")),
        "hpke": {**manifest()["hpke"][hpke_format]["suite"], "format": int(hpke_format)},
        "kex_public_key_alg": int(_current("pubkey", usage="kex")),
        "sig_public_key_alg": int(_current("pubkey", usage="sig")),
        "signature_alg": int(_current("signature")),
        "payload_version": int(_current("payload")),
        "kdf": {"algo": kdf_algo, "params": dict(manifest()["kdf"][kdf_algo]["params"])},
    }


CURRENT_SUITE = _current_suite()
