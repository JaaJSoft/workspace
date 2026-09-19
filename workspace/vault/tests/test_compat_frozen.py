"""The corpus has to be there at all, and it has to be readable.

Everything else in this module proves an implementation against another
implementation. None of that notices a format change: the two sides move
together, agree with each other, and every vault written before the change
stops opening. Only data nobody may rewrite catches that, so the first claim
this file makes is that such data exists.
"""

from django.test import SimpleTestCase

from . import compat


class CorpusPresenceTests(SimpleTestCase):
    def test_at_least_one_version_is_published(self):
        # The whole issue is that no old data exists to replay. An empty
        # corpus root is that state, and it must not pass silently.
        self.assertEqual(compat.versions(), ["v1"])

    def test_every_published_version_loads(self):
        # versions() only reads directory names. A version whose four files
        # never landed would still be listed above, so the corpus that the
        # replays actually open is the one asserted here.
        for version in compat.versions():
            with self.subTest(version=version):
                files = compat.load(version)
                self.assertTrue(files.rows.is_file())
                self.assertTrue(files.archive.startswith(b"VLTARCH"))
                # Both passwords, named apart: one opens the Django
                # account, the other opens the vault, and a corpus that
                # carried one key called "password" would read as a
                # single credential pair that it never was.
                self.assertIn("account_password", files.credentials)
                self.assertIn("vault_master_password", files.credentials)
                self.assertTrue(files.manifest["vaults"])
