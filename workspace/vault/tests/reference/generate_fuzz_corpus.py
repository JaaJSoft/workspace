"""Builds a differential corpus: random inputs, reference outputs.

crypto_vectors.json pins a dozen hand-picked cases. Both bugs found in this
module so far - map keys sorted by UTF-16 length, and integers above the 32-bit
range silently encoded as floats - lived between those cases, in shapes nobody
thought to write down. This generator produces inputs by the hundred instead,
and the browser suite replays them.

Generation is seeded, so a corpus is reproducible and a failure is a case
anyone can regenerate rather than a story about one unlucky run. Raising the
count or changing the seed explores new ground; whatever comes out gets
committed, which turns one exploration into a permanent guard.

Regenerate with:
    uv run python -m workspace.vault.tests.reference.generate_fuzz_corpus
    uv run python -m workspace.vault.tests.reference.generate_fuzz_corpus 7 500
"""

import json
import pathlib
import random
import sys
import unicodedata

from . import primitives
from .encoding import to_base64url

CORPUS_PATH = pathlib.Path(__file__).resolve().parent.parent / "fuzz_corpus.json"

DEFAULT_SEED = 20260819
DEFAULT_COUNT = 240

# Values that sit on an encoding boundary. cbor-x switches representation at
# the 32-bit edges, and the sign changes the major type, so these are where a
# divergence hides.
EDGE_INTEGERS = [
    0,
    1,
    -1,
    23,
    24,
    255,
    256,
    65535,
    65536,
    0x7FFFFFFF,
    0x80000000,
    0xFFFFFFFF,
    0x100000000,
    -255,
    -256,
    -65536,
    -0x80000000,
    2**53 - 1,
    -(2**53) + 1,
]

# The band both implementations refuse: canonically a four-byte negative, which
# cbor-x cannot emit. Generating it would only re-prove the rejection that
# test_reference_crypto already pins.
UNENCODABLE = range(-(2**32), -(2**31))


def _encodable(value: int) -> bool:
    return value not in UNENCODABLE


# Strings chosen for their encoding, not their meaning: an empty string, a key
# whose UTF-8 length differs from its UTF-16 length, both spellings of the same
# accented text, and characters that occupy three and four UTF-8 bytes.
EDGE_STRINGS = [
    "",
    "a",
    "zz",
    "é",  # precomposed e-acute, two UTF-8 bytes
    "é",  # the same text decomposed
    "café",
    "café",
    "€",  # euro sign, three UTF-8 bytes
    "\U0001f512",  # padlock, four UTF-8 bytes, a surrogate pair in UTF-16
    "v",
    "type",
    "vault_uuid",
    "a" * 23,
    "a" * 24,  # CBOR switches to a one-byte length prefix here
    "a" * 255,
]


def _scalar(rng):
    kind = rng.randrange(5)
    if kind == 0:
        return rng.choice(EDGE_INTEGERS)
    if kind == 1:
        while True:
            candidate = rng.randrange(-(2**53) + 1, 2**53 - 1)
            if _encodable(candidate):
                return candidate
    if kind == 2:
        return rng.choice(EDGE_STRINGS)
    if kind == 3:
        return rng.choice([True, False])
    return None


def _structure(rng, depth=0):
    if depth >= 3 or rng.random() < 0.35:
        return _scalar(rng)
    if rng.random() < 0.4:
        return [_structure(rng, depth + 1) for _ in range(rng.randrange(0, 5))]
    keys = rng.sample(EDGE_STRINGS, rng.randrange(1, min(6, len(EDGE_STRINGS))))
    # Two spellings of one accented word are one key once normalised, and the
    # encoder refuses that ambiguity rather than picking a winner.
    unique, seen = [], set()
    for key in keys:
        folded = unicodedata.normalize("NFC", key)
        if folded not in seen:
            seen.add(folded)
            unique.append(key)
    return {key: _structure(rng, depth + 1) for key in unique}


def build_corpus(seed: int = DEFAULT_SEED, count: int = DEFAULT_COUNT) -> dict:
    rng = random.Random(seed)

    cbor_cases = []
    for index in range(count):
        payload = _structure(rng)
        if not isinstance(payload, dict):
            payload = {"v": 1, "payload": payload}
        cbor_cases.append(
            {
                "id": f"cbor-{index:04d}",
                "payload": payload,
                "expected_b64": to_base64url(primitives.canonical_cbor(payload)),
            }
        )

    aead_cases = []
    for index in range(count // 2):
        key = bytes(rng.randrange(256) for _ in range(32))
        iv = bytes(rng.randrange(256) for _ in range(12))
        plaintext = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 200)))
        associated = rng.choice(EDGE_STRINGS).encode("utf-8")
        key_version = rng.choice([0, 1, 255, 256, 65534, 65535])
        kdf_id = rng.choice([0x00, 0x01])
        aead_cases.append(
            {
                "id": f"aead-{index:04d}",
                "key_b64": to_base64url(key),
                "iv_b64": to_base64url(iv),
                "ad_b64": to_base64url(associated),
                "plaintext_b64": to_base64url(plaintext),
                "key_version": key_version,
                "kdf_id": kdf_id,
                "expected_wire_b64": to_base64url(
                    primitives.aead_seal(
                        key,
                        plaintext,
                        associated,
                        iv=iv,
                        key_version=key_version,
                        kdf_id=kdf_id,
                    )
                ),
            }
        )

    return {
        "version": 1,
        "seed": seed,
        "count": count,
        "cbor": cbor_cases,
        "aead": aead_cases,
    }


def main() -> None:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SEED
    count = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_COUNT
    CORPUS_PATH.write_text(
        json.dumps(build_corpus(seed, count), ensure_ascii=False, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {CORPUS_PATH} (seed {seed}, {count} cases)")


if __name__ == "__main__":
    main()
