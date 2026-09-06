from django.test import SimpleTestCase

from .reference import archive


class ArchiveHeaderTests(SimpleTestCase):
    def _header(self, *, magic=archive.MAGIC, version=1, kdf=1, m=65536, t=3, p=2):
        return (
            magic
            + bytes([version, kdf])
            + m.to_bytes(4, "big")
            + t.to_bytes(4, "big")
            + bytes([p])
            + b"\x00" * 32
        )

    def test_reads_the_declared_parameters(self):
        parts = archive.read_header(self._header() + b"payload")
        self.assertEqual(parts["params"], {"m": 65536, "t": 3, "p": 2})
        self.assertEqual(len(parts["header"]), archive.HEADER_LENGTH)
        self.assertEqual(parts["payload"], b"payload")

    def test_refuses_a_file_that_is_not_an_archive(self):
        # A user who hands over the wrong file must read "this is not an
        # archive", not "wrong passphrase" after eight seconds of Argon2.
        with self.assertRaisesMessage(archive.ArchiveError, "not a vault archive"):
            archive.read_header(b"PK\x03\x04" + b"\x00" * 60)

    def test_refuses_parameters_that_would_exhaust_the_machine(self):
        with self.assertRaisesMessage(archive.ArchiveError, "outside [8192, 1048576]"):
            archive.read_header(self._header(m=4194304) + b"x")

    def test_refuses_an_unknown_container_version(self):
        with self.assertRaisesMessage(
            archive.ArchiveError, "unsupported archive version 2"
        ):
            archive.read_header(self._header(version=2) + b"x")
