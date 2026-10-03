import io
import os
import shutil
import tempfile
from unittest import mock

from django.core.exceptions import ImproperlyConfigured, SuspiciousFileOperation
from django.core.files.base import ContentFile
from django.test import SimpleTestCase

from workspace.common.storage.backend import BlobTooLarge, checked_name, temporary_copy
from workspace.common.storage.facade import BlobStorage


def _read(storage, name):
    with storage.open(name, "rb") as handle:
        return handle.read()


class BlobStorageContract:
    """What every backend promises, asserted through BlobStorage.

    A subclass provides ``make_storage(allow_overwrite)``; each backend runs
    the same assertions, so the files code can rely on one behaviour.
    """

    def make_storage(self, *, allow_overwrite=False):
        raise NotImplementedError

    def setUp(self):
        super().setUp()
        self.storage = self.make_storage()

    def save(self, name, data):
        return self.storage.save(name, ContentFile(data))

    # Django's API

    def test_save_open_size_delete(self):
        name = self.save("files/users/alice/doc.txt", b"hello")

        self.assertEqual(name, "files/users/alice/doc.txt")
        self.assertTrue(self.storage.exists(name))
        self.assertEqual(self.storage.size(name), 5)
        self.assertEqual(_read(self.storage, name), b"hello")

        self.storage.delete(name)
        self.assertFalse(self.storage.exists(name))

    def test_a_taken_name_is_kept_beside_by_default(self):
        first = self.save("mail/invoice.pdf", b"first")
        second = self.save("mail/invoice.pdf", b"second")

        self.assertNotEqual(first, second)
        self.assertEqual(_read(self.storage, first), b"first")
        self.assertEqual(_read(self.storage, second), b"second")

    def test_a_taken_name_is_replaced_when_overwrite_is_allowed(self):
        storage = self.make_storage(allow_overwrite=True)
        storage.save("files/doc.txt", ContentFile(b"first"))
        name = storage.save("files/doc.txt", ContentFile(b"second"))

        self.assertEqual(name, "files/doc.txt")
        self.assertEqual(_read(storage, name), b"second")

    def test_listdir(self):
        self.save("chat/conv/a.png", b"a")
        self.save("chat/conv/sub/b.png", b"b")

        directories, files = self.storage.listdir("chat/conv")

        self.assertEqual(directories, ["sub"])
        self.assertEqual(files, ["a.png"])

    # Directories

    def test_a_saved_blob_makes_its_parents_directories(self):
        self.save("files/users/alice/Docs/a.txt", b"a")

        self.assertTrue(self.storage.is_dir("files/users/alice/Docs"))
        self.assertTrue(self.storage.is_dir("files/users/alice"))
        self.assertFalse(self.storage.is_file("files/users/alice/Docs"))
        self.assertTrue(self.storage.is_file("files/users/alice/Docs/a.txt"))
        self.assertFalse(self.storage.is_dir("files/users/alice/Docs/a.txt"))

    def test_nothing_stored_is_neither_a_directory_nor_a_file(self):
        self.assertFalse(self.storage.is_dir("files/users/nobody"))
        self.assertFalse(self.storage.is_file("files/users/nobody"))

    def test_make_dir_keeps_an_empty_directory(self):
        self.storage.make_dir("files/users/alice/Empty/Nested")

        self.assertTrue(self.storage.is_dir("files/users/alice/Empty/Nested"))
        self.assertTrue(self.storage.is_dir("files/users/alice/Empty"))
        self.assertEqual(self.storage.scan("files/users/alice/Empty/Nested"), [])

    def test_make_dir_on_an_existing_directory_is_harmless(self):
        self.save("files/users/alice/Docs/a.txt", b"a")
        self.storage.make_dir("files/users/alice/Docs")

        self.assertEqual(_read(self.storage, "files/users/alice/Docs/a.txt"), b"a")

    def test_scan_lists_one_level(self):
        self.save("files/users/alice/a.txt", b"a")
        self.save("files/users/alice/Docs/b.txt", b"b")
        self.storage.make_dir("files/users/alice/Empty")

        entries = {e.name: e for e in self.storage.scan("files/users/alice")}

        self.assertEqual(set(entries), {"a.txt", "Docs", "Empty"})
        self.assertTrue(entries["a.txt"].is_file)
        self.assertFalse(entries["a.txt"].is_dir)
        self.assertTrue(entries["Docs"].is_dir)
        self.assertTrue(entries["Empty"].is_dir)
        self.assertFalse(entries["Docs"].is_file)

    def test_remove_dir_if_empty(self):
        self.storage.make_dir("files/users/alice/Empty")

        self.assertTrue(self.storage.remove_dir_if_empty("files/users/alice/Empty"))
        self.assertFalse(self.storage.is_dir("files/users/alice/Empty"))

    def test_remove_dir_if_empty_keeps_a_directory_holding_anything(self):
        self.save("files/users/alice/Docs/a.txt", b"a")

        self.assertFalse(self.storage.remove_dir_if_empty("files/users/alice/Docs"))
        self.assertEqual(_read(self.storage, "files/users/alice/Docs/a.txt"), b"a")

    def test_remove_dir_if_empty_on_nothing_removes_nothing(self):
        self.assertFalse(self.storage.remove_dir_if_empty("files/users/alice/Gone"))

    def test_delete_prefix_removes_the_whole_subtree(self):
        self.save("files/users/alice/Docs/a.txt", b"a")
        self.save("files/users/alice/Docs/Sub/b.txt", b"b")
        self.storage.make_dir("files/users/alice/Docs/Empty")

        self.assertTrue(self.storage.delete_prefix("files/users/alice/Docs"))

        self.assertFalse(self.storage.is_dir("files/users/alice/Docs"))
        self.assertFalse(self.storage.exists("files/users/alice/Docs/Sub/b.txt"))

    def test_delete_prefix_spares_a_sibling_sharing_its_leading_characters(self):
        self.save("files/users/alice/Docs/a.txt", b"a")
        self.save("files/users/alice/Docs 2/b.txt", b"b")
        self.save("files/users/alice/Docsx.txt", b"c")

        self.storage.delete_prefix("files/users/alice/Docs")

        self.assertEqual(_read(self.storage, "files/users/alice/Docs 2/b.txt"), b"b")
        self.assertEqual(_read(self.storage, "files/users/alice/Docsx.txt"), b"c")

    def test_delete_prefix_on_nothing_deletes_nothing(self):
        self.assertFalse(self.storage.delete_prefix("files/users/alice/Gone"))

    def test_names_that_reach_the_root_or_above_are_refused(self):
        self.save("files/users/alice/a.txt", b"a")
        for name in ("", ".", "..", "/files", "files/..", "files/../x", "files//x"):
            with self.subTest(name=name):
                with self.assertRaises(SuspiciousFileOperation):
                    self.storage.delete_prefix(name)
                with self.assertRaises(SuspiciousFileOperation):
                    self.storage.move(name, "trash/x")

        self.assertEqual(_read(self.storage, "files/users/alice/a.txt"), b"a")

    # Moves

    def test_move_a_blob_into_a_new_directory(self):
        self.save("files/users/alice/a.txt", b"a")

        self.storage.move("files/users/alice/a.txt", "trash/users/alice/x/a.txt")

        self.assertFalse(self.storage.exists("files/users/alice/a.txt"))
        self.assertEqual(_read(self.storage, "trash/users/alice/x/a.txt"), b"a")

    def test_move_a_directory_with_its_content(self):
        self.save("files/users/alice/Docs/a.txt", b"a")
        self.save("files/users/alice/Docs/Sub/b.txt", b"b")
        self.save("files/users/alice/Docs 2/c.txt", b"c")

        self.storage.move("files/users/alice/Docs", "files/users/alice/Archive/Docs")

        self.assertFalse(self.storage.is_dir("files/users/alice/Docs"))
        self.assertEqual(
            _read(self.storage, "files/users/alice/Archive/Docs/Sub/b.txt"), b"b"
        )
        self.assertEqual(
            _read(self.storage, "files/users/alice/Archive/Docs/a.txt"), b"a"
        )
        self.assertEqual(_read(self.storage, "files/users/alice/Docs 2/c.txt"), b"c")

    def test_move_an_empty_directory(self):
        self.storage.make_dir("files/users/alice/Empty")

        self.storage.move("files/users/alice/Empty", "files/users/alice/Renamed")

        self.assertFalse(self.storage.is_dir("files/users/alice/Empty"))
        self.assertTrue(self.storage.is_dir("files/users/alice/Renamed"))

    # Writes that only appear whole

    def test_a_staged_write_appears_on_commit(self):
        writer = self.storage.staged_writer("files/users/alice/new.bin")
        writer.write(b"abc")
        writer.write(bytearray(b"def"))

        self.assertFalse(self.storage.exists("files/users/alice/new.bin"))
        writer.commit()

        self.assertEqual(_read(self.storage, "files/users/alice/new.bin"), b"abcdef")

    def test_a_staged_write_replaces_the_previous_blob_only_on_commit(self):
        self.save("files/users/alice/doc.txt", b"old")
        writer = self.storage.staged_writer("files/users/alice/doc.txt")
        writer.write(b"new")

        self.assertEqual(_read(self.storage, "files/users/alice/doc.txt"), b"old")
        writer.commit()
        self.assertEqual(_read(self.storage, "files/users/alice/doc.txt"), b"new")

    def test_an_aborted_staged_write_leaves_the_previous_blob_and_nothing_else(self):
        self.save("files/users/alice/doc.txt", b"old")
        writer = self.storage.staged_writer("files/users/alice/doc.txt")
        writer.write(b"partial")
        writer.abort()

        self.assertEqual(_read(self.storage, "files/users/alice/doc.txt"), b"old")
        self.assertEqual(
            [e.name for e in self.storage.scan("files/users/alice")], ["doc.txt"]
        )

    def test_replace(self):
        self.save("files/users/alice/doc.txt", b"old")

        name = self.storage.replace("files/users/alice/doc.txt", ContentFile(b"new"))

        self.assertEqual(name, "files/users/alice/doc.txt")
        self.assertEqual(_read(self.storage, name), b"new")
        self.assertEqual(
            [e.name for e in self.storage.scan("files/users/alice")], ["doc.txt"]
        )

    # Local paths, for external tools

    def test_local_path_holds_the_bytes(self):
        self.save("files/users/alice/clip.webm", b"video bytes")

        with self.storage.local_path(
            "files/users/alice/clip.webm", max_bytes=1024
        ) as path:
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), b"video bytes")

    def test_local_path_of_a_missing_blob(self):
        with self.assertRaises(FileNotFoundError):
            with self.storage.local_path("files/users/alice/gone.webm", max_bytes=1024):
                pass


class LocalBlobStorageTests(BlobStorageContract, SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="workspace-test-blobs-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        super().setUp()

    def make_storage(self, *, allow_overwrite=False):
        return BlobStorage(location=self.root, allow_overwrite=allow_overwrite)

    def test_path_is_the_file_under_the_root(self):
        name = self.save("files/users/alice/doc.txt", b"x")

        self.assertEqual(
            self.storage.path(name),
            os.path.join(self.root, "files", "users", "alice", "doc.txt"),
        )

    def test_local_path_is_the_blob_itself(self):
        name = self.save("files/users/alice/clip.webm", b"x" * 2048)

        with self.storage.local_path(name, max_bytes=1) as path:
            self.assertEqual(path, self.storage.path(name))

    def test_a_staged_write_survives_short_os_writes(self):
        """POSIX allows os.write to write fewer bytes than requested; the
        writer must loop until all of it is on disk or bytes are silently
        dropped."""
        real_write = os.write

        def short_write(fd, data):
            return real_write(fd, bytes(data)[:5])

        writer = self.storage.staged_writer("upload.bin")
        with mock.patch(
            "workspace.common.storage.local.os.write", side_effect=short_write
        ):
            writer.write(b"0123456789ABCDEF")
        writer.commit()

        self.assertEqual(_read(self.storage, "upload.bin"), b"0123456789ABCDEF")

    def test_aborting_after_the_staged_file_vanished_is_safe(self):
        writer = self.storage.staged_writer("upload.bin")
        writer.write(b"partial")
        os.close(writer._fd)
        writer._fd = None
        os.unlink(writer._temp_path)

        writer.abort()

        self.assertEqual(os.listdir(self.root), [])

    def test_replace_survives_a_save_that_dies_halfway(self):
        """The name holds one complete version or the other, never a stump.

        A disk truncates in place, so a save that dies after writing part of
        the bytes would otherwise leave neither the old version nor the new.
        """
        self.save("files/doc.txt", b"the original bytes")

        def write_then_die(name, content, max_length=None):
            with open(self.storage.path(name), "wb") as handle:
                handle.write(b"a new and lon")
            raise OSError("connection reset mid-transfer")

        with (
            mock.patch.object(self.storage, "save", write_then_die),
            self.assertRaises(OSError),
        ):
            self.storage.replace("files/doc.txt", ContentFile(b"a new and longer body"))

        self.assertEqual(_read(self.storage, "files/doc.txt"), b"the original bytes")
        self.assertEqual(os.listdir(os.path.join(self.root, "files")), ["doc.txt"])

    def test_scan_lists_a_symlink_as_neither_directory_nor_file(self):
        self.storage.make_dir("files/users/alice")
        target = os.path.join(self.root, "elsewhere")
        os.mkdir(target)
        try:
            os.symlink(target, self.storage.path("files/users/alice/link"))
        except OSError, NotImplementedError:
            self.skipTest("symlinks are not available on this platform")

        (entry,) = self.storage.scan("files/users/alice")

        self.assertEqual(entry.name, "link")
        self.assertFalse(entry.is_dir)
        self.assertFalse(entry.is_file)


class BlobStorageConfigurationTests(SimpleTestCase):
    def test_an_unknown_backend_is_a_configuration_error(self):
        with self.assertRaises(ImproperlyConfigured):
            BlobStorage(backend="ftp")

    def test_deconstructs_to_its_options(self):
        path, args, kwargs = BlobStorage(allow_overwrite=True).deconstruct()

        self.assertEqual(path, "workspace.common.storage.facade.BlobStorage")
        self.assertEqual(kwargs, {"allow_overwrite": True})


class CheckedNameTests(SimpleTestCase):
    def test_a_plain_name_passes(self):
        self.assertEqual(
            checked_name("files/users/alice/a b.txt"), "files/users/alice/a b.txt"
        )

    def test_backslashes_count_as_separators(self):
        for name in ("files\\..\\..", "\\files"):
            with self.subTest(name=name), self.assertRaises(SuspiciousFileOperation):
                checked_name(name)


class _RemoteBackend:
    """A backend with no local paths: only size and open."""

    def __init__(self, data):
        self._data = data

    def size(self, name):
        return len(self._data)

    def open(self, name, mode="rb"):
        return io.BytesIO(self._data)


class TemporaryCopyTests(SimpleTestCase):
    def test_the_copy_holds_the_bytes_and_goes_away(self):
        with temporary_copy(
            _RemoteBackend(b"video bytes"), "clip", max_bytes=64
        ) as path:
            with open(path, "rb") as copy:
                self.assertEqual(copy.read(), b"video bytes")

        self.assertFalse(os.path.exists(path))

    def test_a_copy_past_the_bound_is_refused(self):
        with self.assertRaises(BlobTooLarge):
            with temporary_copy(_RemoteBackend(b"12345"), "clip", max_bytes=4):
                pass
