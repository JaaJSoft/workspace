import io
import zipfile

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
