import json
from pathlib import Path

from django.test import SimpleTestCase

from .reference import archive

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
        with self.assertRaises(Exception):
            archive.open_archive(self.blob, "not the phrase")

    def test_a_flipped_byte_in_the_public_header_is_refused(self):
        # The whole header is the seal's associated data, so tampering fails
        # as tampering. Byte 20 is inside the salt.
        altered = bytearray(self.blob)
        altered[20] ^= 0xFF
        with self.assertRaises(Exception):
            archive.open_archive(bytes(altered), self.vector["passphrase"])
