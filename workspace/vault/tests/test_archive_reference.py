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

    def test_refuses_an_almost_right_magic(self):
        with self.assertRaisesMessage(archive.ArchiveError, "not a vault archive"):
            archive.read_header(self._header(magic=b"VLTARCX") + b"x")

    def test_refuses_a_file_too_short_to_hold_a_header(self):
        # Truncated before the salt ends: slicing on would yield a short salt
        # and derive a key from it rather than say the file is incomplete.
        with self.assertRaisesMessage(archive.ArchiveError, "not a vault archive"):
            archive.read_header(self._header()[:-1])

    def test_refuses_parameters_that_would_exhaust_the_machine(self):
        with self.assertRaisesMessage(archive.ArchiveError, "outside [8192, 1048576]"):
            archive.read_header(self._header(m=4194304) + b"x")

    def test_refuses_every_cost_parameter_outside_its_bounds(self):
        # Each bound is checked on both sides: a table entry no test exercises
        # is a bound that can be widened by accident.
        for kwargs, message in [
            ({"m": 4096}, "archive m is 4096, outside [8192, 1048576]"),
            ({"m": 2097152}, "archive m is 2097152, outside [8192, 1048576]"),
            ({"t": 0}, "archive t is 0, outside [1, 10]"),
            ({"t": 11}, "archive t is 11, outside [1, 10]"),
            ({"p": 0}, "archive p is 0, outside [1, 4]"),
            ({"p": 9}, "archive p is 9, outside [1, 4]"),
        ]:
            with self.subTest(**kwargs):
                with self.assertRaisesMessage(archive.ArchiveError, message):
                    archive.read_header(self._header(**kwargs) + b"x")

    def test_refuses_an_unknown_container_version(self):
        with self.assertRaisesMessage(
            archive.ArchiveError, "unsupported archive version 2"
        ):
            archive.read_header(self._header(version=2) + b"x")

    def test_refuses_an_unknown_container_kdf(self):
        # The kdf byte says which derivation the file was written with. A
        # reader that ignored it would run Argon2id over a file that declared
        # something else and report the mismatch as a wrong passphrase.
        with self.assertRaisesMessage(
            archive.ArchiveError, "unsupported archive kdf 2"
        ):
            archive.read_header(self._header(kdf=2) + b"x")
