import json
from pathlib import Path

import cbor2
from cryptography.exceptions import InvalidTag
from django.test import SimpleTestCase

from .reference import archive, primitives

VECTOR = Path(__file__).parent / "fixtures" / "archive_vector.json"


class ArchiveRoundTripTests(SimpleTestCase):
    """The archive opens for something that has never run the browser's code.

    That is the whole point of the round trip: two implementations agreeing is
    evidence, one implementation agreeing with itself is not."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vector = json.loads(VECTOR.read_text(encoding="utf-8"))
        cls.blob = bytes.fromhex(cls.vector["archive_hex"])

    def test_the_browser_archive_opens_with_its_passphrase(self):
        opened = archive.open_archive(self.blob, self.vector["passphrase"])
        self.assertEqual(opened, self.vector["tree"])

    def test_the_payload_declares_the_hkdf_step_it_went_through(self):
        # The archive key is an HKDF output, and the payload's third byte is
        # where the ciphertext says so. Nothing in the seal covers that byte -
        # the associated data is the public header - so a writer that left the
        # kdf id on its default would still open here. Only this assertion
        # catches the claim being false.
        parts = archive.read_header(self.blob)
        self.assertEqual(parts["payload"][2], 0x01, "kdf id: hkdf-sha256")

    def test_a_wrong_passphrase_does_not_open_it(self):
        # InvalidTag and not Exception: a typo in this test would raise
        # something else, and a negative test that accepts any crash reports
        # its own bugs as a pass.
        with self.assertRaises(InvalidTag):
            archive.open_archive(self.blob, "not the phrase")

    def test_an_altered_public_header_does_not_open(self):
        # Byte 12 is the low byte of m, so the altered file still declares
        # parameters inside the bounds and reaches the seal. What this holds is
        # that no edit of the header opens the archive - not which of the two
        # mechanisms refused it, since every header byte past the magic feeds
        # the derivation as well as the associated data.
        altered = bytearray(self.blob)
        altered[12] ^= 0x01
        with self.assertRaises(InvalidTag):
            archive.open_archive(bytes(altered), self.vector["passphrase"])

    def test_the_seal_is_bound_to_the_whole_public_header(self):
        # The key is held fixed here, which is what isolates the property: only
        # the associated data changes between the two calls. Without the header
        # bound in, a flipped byte would surface as a wrong passphrase rather
        # than as a file that has been altered.
        parts = archive.read_header(self.blob)
        key = archive.derive_archive_key(
            self.vector["passphrase"], parts["salt"], parts["params"]
        )
        opened = primitives.aead_open(key, parts["payload"], parts["header"])
        self.assertEqual(cbor2.loads(opened), self.vector["tree"])
        flipped = bytearray(parts["header"])
        flipped[0] ^= 0x01
        for name, associated_data in [
            ("empty", b""),
            ("salt only", parts["salt"]),
            ("one flipped byte", bytes(flipped)),
        ]:
            with self.subTest(name):
                with self.assertRaises(InvalidTag):
                    primitives.aead_open(key, parts["payload"], associated_data)
