"""Regression tests for Content-Disposition filename sanitization.

The /content and /download endpoints build the Content-Disposition header by
interpolating File.name into a quoted-string parameter. File.name is a CharField
that accepts any printable character, including double quotes and CRLF, so the
raw interpolation is a header-injection vector. These tests pin the sanitizer.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework.test import APITestCase

from workspace.files.models import File

User = get_user_model()


class ContentDispositionSanitizationTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='disp-user', email='disp@example.com', password='pass123'
        )
        self.client.force_authenticate(user=self.user)

    def _make_file(self, name, payload=b'data', mime='text/plain'):
        f = File(
            owner=self.user, name=name,
            node_type=File.NodeType.FILE, mime_type=mime,
        )
        f.content = ContentFile(payload, name='blob.bin')
        f.size = len(payload)
        f.save()
        return f

    def assert_safe_disposition(self, header_value):
        # No raw CR/LF -> no response-splitting.
        self.assertNotIn('\r', header_value)
        self.assertNotIn('\n', header_value)
        # No unescaped double-quote that would close the quoted-string
        # parameter early. Allow the surrounding `"..."` quotes; the inner
        # quote from the filename must be backslash-escaped.
        inner = header_value.split('filename="', 1)[1].rsplit('"', 1)[0]
        # Every literal `"` inside must be preceded by a backslash.
        i = 0
        while i < len(inner):
            if inner[i] == '"':
                self.assertGreater(i, 0)
                self.assertEqual(inner[i - 1], '\\')
            i += 1

    def test_content_range_response_sanitizes_filename(self):
        f = self._make_file('clip";evil\r\nX-Injected: yes.mp4',
                            payload=b'A' * 64, mime='video/mp4')
        resp = self.client.get(f'/api/v1/files/{f.uuid}/content',
                               HTTP_RANGE='bytes=0-7')
        self.assertEqual(resp.status_code, 206)
        self.assert_safe_disposition(resp['Content-Disposition'])
        # And the injected header MUST NOT appear as its own header.
        self.assertNotIn('X-Injected', resp)

    def test_content_text_response_sanitizes_filename(self):
        f = self._make_file('note";\r\nX-Injected: yes.txt',
                            payload=b'hello', mime='text/plain')
        resp = self.client.get(f'/api/v1/files/{f.uuid}/content')
        self.assertEqual(resp.status_code, 200)
        self.assert_safe_disposition(resp['Content-Disposition'])
        self.assertNotIn('X-Injected', resp)

    def test_content_binary_response_sanitizes_filename(self):
        f = self._make_file('blob";\r\nX-Injected: yes.bin',
                            payload=b'\x00\x01\x02', mime='application/octet-stream')
        resp = self.client.get(f'/api/v1/files/{f.uuid}/content')
        self.assertEqual(resp.status_code, 200)
        self.assert_safe_disposition(resp['Content-Disposition'])
        self.assertNotIn('X-Injected', resp)

    def test_folder_zip_download_sanitizes_filename(self):
        # Folder name with header-injection chars. The child is saved under a
        # safe folder name first (so the FieldFile storage path is valid on
        # all platforms), then the folder name is rewritten via a direct DB
        # update that bypasses save() - this models a name introduced before
        # any future tightening and avoids touching the filesystem path.
        folder = File.objects.create(
            owner=self.user, name='Reports', node_type=File.NodeType.FOLDER,
        )
        child = File(
            owner=self.user, name='data.txt',
            node_type=File.NodeType.FILE, parent=folder, mime_type='text/plain',
        )
        child.content = ContentFile(b'file content', name='data.txt')
        child.size = 12
        child.save()
        File.objects.filter(pk=folder.pk).update(name='Reports";\r\nX-Injected: yes')

        resp = self.client.get(f'/api/v1/files/{folder.uuid}/download')
        self.assertEqual(resp.status_code, 200)
        self.assert_safe_disposition(resp['Content-Disposition'])
        self.assertNotIn('X-Injected', resp)
