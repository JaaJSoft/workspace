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
