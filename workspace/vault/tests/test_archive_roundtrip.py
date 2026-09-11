import json
from pathlib import Path

import cbor2
from cryptography.exceptions import InvalidTag
from django.test import SimpleTestCase

from .reference import archive, primitives

FIXTURES = Path(__file__).parent / "fixtures"
VECTOR = FIXTURES / "archive_vector.json"
LOW_COST_VECTOR = FIXTURES / "archive_vector_low_cost.json"


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


class ArchiveCostParameterTests(SimpleTestCase):
    """The cost parameters travel in the file, and the reader honours them.

    Everything else in this module is written at the defaults, so a reader
    that never looked at bytes 9-17 and always derived at today's constants
    would open every one of those archives. This vector is written at other
    parameters, which is the only way to tell the two readers apart."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vector = json.loads(LOW_COST_VECTOR.read_text(encoding="utf-8"))
        cls.blob = bytes.fromhex(cls.vector["archive_hex"])

    def test_the_header_declares_the_parameters_it_was_written_at(self):
        parts = archive.read_header(self.blob)
        self.assertEqual(parts["params"], self.vector["params"])

    def test_those_parameters_are_none_of_the_defaults(self):
        # Without this the test below is worth nothing: the day a default
        # moves onto one of these values, a reader that ignores the header
        # starts passing again and nothing says so.
        for name, value in self.vector["params"].items():
            with self.subTest(name):
                self.assertNotEqual(value, primitives.ARGON2_PARAMS[name])

    def test_an_archive_written_at_other_parameters_opens_at_those_parameters(self):
        # The container's central claim. A reader that bounds-checks the
        # declared parameters and then derives at the defaults anyway gets a
        # different key and fails the tag here.
        opened = archive.open_archive(self.blob, self.vector["passphrase"])
        self.assertEqual(opened, self.vector["tree"])
