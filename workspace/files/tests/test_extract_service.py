import io
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from workspace.files.models import File
from workspace.files.services import FileService
from workspace.files.services.extract import extract_zip

User = get_user_model()


def _make_zip(entries):
    """Build an in-memory ZIP. ``entries`` is a list of (name, bytes_or_None) tuples;
    None means create a directory entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries:
            if data is None:
                zi = zipfile.ZipInfo(name if name.endswith('/') else name + '/')
                zf.writestr(zi, b'')
            else:
                zf.writestr(name, data)
    buf.seek(0)
    return buf.read()


def _make_symlink_zip(name, target):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo(name)
        # Symlink bit in external_attr (high 16 bits = unix mode, 0o120000 = symlink)
        zi.external_attr = (0o120777 & 0xFFFF) << 16
        zf.writestr(zi, target)
    buf.seek(0)
    return buf.read()


class ExtractZipServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='alice', email='alice@example.com', password='pw'
        )
        self.dest = FileService.create_folder(self.user, 'dest')

    def _make_archive_file(self, payload, name='archive.zip', mime='application/zip'):
        return FileService.create_file(
            self.user, name, parent=None,
            content=ContentFile(payload, name=name),
            mime_type=mime,
        )

    def test_extract_creates_files_and_subfolders(self):
        payload = _make_zip([
            ('hello.txt', b'hello world'),
            ('sub/', None),
            ('sub/nested.txt', b'nested content'),
        ])
        archive = self._make_archive_file(payload)

        result = extract_zip(archive, self.dest, acting_user=self.user)

        self.assertEqual(result['destination_uuid'], str(self.dest.uuid))
        self.assertEqual(result['files_created'], 2)

        hello = File.objects.get(parent=self.dest, name='hello.txt')
        self.assertEqual(hello.content.read(), b'hello world')

        sub = File.objects.get(parent=self.dest, name='sub', node_type='folder')

        nested = File.objects.get(parent=sub, name='nested.txt')
        self.assertEqual(nested.content.read(), b'nested content')

    def test_extract_rejects_zip_slip(self):
        payload = _make_zip([('../evil.txt', b'bad')])
        archive = self._make_archive_file(payload)

        with self.assertRaises(ValueError) as ctx:
            extract_zip(archive, self.dest, acting_user=self.user)
        self.assertIn('unsafe', str(ctx.exception).lower())
        self.assertEqual(File.objects.filter(parent=self.dest).count(), 0)

    def test_extract_rejects_absolute_path(self):
        payload = _make_zip([('/etc/passwd', b'bad')])
        archive = self._make_archive_file(payload)
        with self.assertRaises(ValueError):
            extract_zip(archive, self.dest, acting_user=self.user)

    def test_extract_rejects_windows_drive(self):
        payload = _make_zip([('C:/Windows/evil.dll', b'bad')])
        archive = self._make_archive_file(payload)
        with self.assertRaises(ValueError):
            extract_zip(archive, self.dest, acting_user=self.user)

    def test_extract_ignores_symlinks(self):
        payload = _make_symlink_zip('link', '/etc/passwd')
        archive = self._make_archive_file(payload)

        result = extract_zip(archive, self.dest, acting_user=self.user)
        self.assertEqual(result['files_created'], 0)
        self.assertFalse(File.objects.filter(parent=self.dest, name='link').exists())

    @override_settings(FILES_EXTRACT_MAX_BYTES=10)
    def test_extract_size_limit(self):
        payload = _make_zip([('big.txt', b'X' * 1000)])
        archive = self._make_archive_file(payload)
        with self.assertRaises(ValueError) as ctx:
            extract_zip(archive, self.dest, acting_user=self.user)
        self.assertIn('too large', str(ctx.exception).lower())

    @override_settings(FILES_EXTRACT_MAX_ENTRIES=2)
    def test_extract_entry_limit(self):
        payload = _make_zip([
            ('a.txt', b'a'),
            ('b.txt', b'b'),
            ('c.txt', b'c'),
        ])
        archive = self._make_archive_file(payload)
        with self.assertRaises(ValueError) as ctx:
            extract_zip(archive, self.dest, acting_user=self.user)
        self.assertIn('too many', str(ctx.exception).lower())

    def test_extract_rejects_non_zip_mime(self):
        archive = self._make_archive_file(b'not a zip', mime='text/plain', name='note.txt')
        with self.assertRaises(ValueError) as ctx:
            extract_zip(archive, self.dest, acting_user=self.user)
        self.assertIn('zip', str(ctx.exception).lower())

    def test_extract_rejects_corrupted_archive(self):
        archive = self._make_archive_file(b'PK\x03\x04 garbage', mime='application/zip')
        with self.assertRaises(ValueError) as ctx:
            extract_zip(archive, self.dest, acting_user=self.user)
        self.assertIn('corrupt', str(ctx.exception).lower())

    def test_extract_is_atomic_on_failure(self):
        payload = _make_zip([
            ('ok.txt', b'ok'),
            ('../evil.txt', b'bad'),
        ])
        archive = self._make_archive_file(payload)
        with self.assertRaises(ValueError):
            extract_zip(archive, self.dest, acting_user=self.user)
        self.assertEqual(File.objects.filter(parent=self.dest).count(), 0)

    @override_settings(FILES_EXTRACT_MAX_BYTES=1024)
    def test_extract_size_limit_enforced_on_actual_bytes_not_header(self):
        """A malicious ZIP can under-report ``file_size`` in the central directory
        to bypass a header-based pre-check, then decompress to a much larger blob.
        Defence: the cap must be enforced on the *streamed decompressed bytes*,
        not on the untrusted header. We simulate the bypass by making the entry
        claim ``file_size = 1`` while the underlying decompressor streams 3 KiB.
        With the cap at 1 KiB, the extractor must raise ``Archive too large``."""
        payload = _make_zip([('big.txt', b'X' * 3000)])
        archive = self._make_archive_file(payload)

        # Stream that yields way more bytes than the header claims.
        class FakeStream(io.RawIOBase):
            def __init__(self):
                self._chunks = [b'X' * 1024, b'Y' * 1024, b'Z' * 1024]
            def read(self, n=-1):
                return self._chunks.pop(0) if self._chunks else b''
            def readable(self):
                return True

        orig_open = zipfile.ZipFile.open

        def lying_open(self, name_or_info, *args, **kwargs):
            # Honour the original `open` for everything except our target.
            target = name_or_info.filename if hasattr(name_or_info, 'filename') else name_or_info
            if target == 'big.txt':
                return FakeStream()
            return orig_open(self, name_or_info, *args, **kwargs)

        orig_infolist = zipfile.ZipFile.infolist

        def lying_infolist(self):
            entries = orig_infolist(self)
            for e in entries:
                # Under-report file_size in the header so a naive pre-check passes.
                e.file_size = 1
            return entries

        with patch.object(zipfile.ZipFile, 'open', lying_open), \
             patch.object(zipfile.ZipFile, 'infolist', lying_infolist):
            with self.assertRaises(ValueError) as ctx:
                extract_zip(archive, self.dest, acting_user=self.user)
        self.assertIn('too large', str(ctx.exception).lower())

    def test_extract_to_root_when_dest_is_none(self):
        payload = _make_zip([
            ('hello.txt', b'hi'),
            ('sub/nested.txt', b'nested'),
        ])
        archive = self._make_archive_file(payload)

        result = extract_zip(archive, None, acting_user=self.user)

        self.assertIsNone(result['destination_uuid'])
        self.assertEqual(result['files_created'], 2)

        hello = File.objects.get(
            owner=self.user, parent=None, name='hello.txt', node_type='file',
        )
        self.assertEqual(hello.content.read(), b'hi')

        sub = File.objects.get(
            owner=self.user, parent=None, name='sub', node_type='folder',
        )
        nested = File.objects.get(parent=sub, name='nested.txt')
        self.assertEqual(nested.content.read(), b'nested')

    def test_extract_accepts_x_zip_compressed_mime(self):
        payload = _make_zip([('hello.txt', b'hi')])
        archive = self._make_archive_file(payload, mime='application/x-zip-compressed')

        result = extract_zip(archive, self.dest, acting_user=self.user)

        self.assertGreater(result['files_created'], 0)
        self.assertTrue(File.objects.filter(parent=self.dest, name='hello.txt').exists())

    def test_extract_cleans_up_blobs_on_rollback(self):
        """When extraction fails partway through, blobs already written to
        storage before the failure must be deleted (no orphans). The atomic
        block only rolls back DB rows - storage blobs need an explicit unlink.
        """
        from unittest.mock import patch

        payload = _make_zip([
            ('ok.txt', b'first entry, will succeed'),
            ('../evil.txt', b'second entry, triggers zip-slip rejection'),
        ])
        archive = self._make_archive_file(payload)

        storage = File._meta.get_field('content').storage
        delete_calls = []
        orig_delete = storage.delete

        def tracking_delete(name):
            delete_calls.append(name)
            return orig_delete(name)

        with patch.object(storage, 'delete', side_effect=tracking_delete):
            with self.assertRaises(ValueError):
                extract_zip(archive, self.dest, acting_user=self.user)

        # DB: zero rows under dest (atomic rollback).
        self.assertEqual(File.objects.filter(parent=self.dest).count(), 0)

        # Storage: the blob for 'ok.txt' that was created before the rejection
        # must have been deleted (storage.delete called for it).
        self.assertTrue(
            any('ok.txt' in c for c in delete_calls),
            f"Expected a cleanup delete for the 'ok.txt' blob, got: {delete_calls}",
        )

    def test_extract_streams_entries_via_tempfile_not_contentfile(self):
        """Regression test: large entries must flow through a temp file, not be
        buffered fully in RAM via ContentFile. Mirrors the outbound streaming
        pattern in ``ContentMixin._build_zip_stream`` (constant-RAM downloads)."""
        from workspace.files.services import extract as extract_mod

        payload = _make_zip([('hello.txt', b'hello world')])
        archive = self._make_archive_file(payload)

        with patch.object(
            extract_mod, 'TemporaryUploadedFile',
            wraps=extract_mod.TemporaryUploadedFile,
        ) as spy:
            result = extract_zip(archive, self.dest, acting_user=self.user)

        self.assertEqual(result['files_created'], 1)
        self.assertGreaterEqual(
            spy.call_count, 1,
            "TemporaryUploadedFile must be instantiated for each extracted entry "
            "to keep RAM bounded (see ContentMixin._build_zip_stream for the "
            "outbound streaming pattern this mirrors).",
        )

    def test_extract_reuses_existing_intermediate_folder(self):
        payload = _make_zip([
            ('sub/a.txt', b'a'),
            ('sub/b.txt', b'b'),
        ])
        archive = self._make_archive_file(payload)
        extract_zip(archive, self.dest, acting_user=self.user)
        subs = File.objects.filter(parent=self.dest, name='sub', node_type='folder')
        self.assertEqual(subs.count(), 1)
        self.assertEqual(File.objects.filter(parent=subs.first()).count(), 2)
