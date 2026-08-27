from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import File
from workspace.files.services.text_extraction import BODY_CAP, extract_text

User = get_user_model()


class ExtractTextTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")

    def _file(self, name, mime, payload):
        return File.objects.create(
            name=name,
            node_type=File.NodeType.FILE,
            mime_type=mime,
            owner=self.user,
            content=ContentFile(payload, name=name),
        )

    def test_markdown_body_is_extracted(self):
        f = self._file("note.md", "text/markdown", b"# Title\n\nThe kraken sleeps.")
        self.assertIn("kraken", extract_text(f))

    def test_plain_text_is_extracted(self):
        f = self._file("a.txt", "text/plain", b"quarterly revenue")
        self.assertEqual(extract_text(f), "quarterly revenue")

    def test_csv_is_extracted(self):
        f = self._file("a.csv", "text/csv", b"name,city\nada,london")
        self.assertIn("london", extract_text(f))

    def test_unknown_text_subtype_is_extracted(self):
        f = self._file("a.rst", "text/x-rst", b"reStructured content")
        self.assertIn("reStructured", extract_text(f))

    def test_mime_parameters_are_ignored(self):
        f = self._file("a.txt", "text/plain; charset=utf-8", b"parameterised")
        self.assertEqual(extract_text(f), "parameterised")

    def test_html_tags_are_stripped(self):
        payload = b"<html><body><p>Visible <b>text</b></p></body></html>"
        f = self._file("a.html", "text/html", payload)
        body = extract_text(f)
        self.assertIn("Visible", body)
        self.assertIn("text", body)
        self.assertNotIn("<p>", body)

    def test_html_script_and_style_bodies_are_dropped(self):
        payload = (
            b"<style>.a{color:red}</style>"
            b"<script>var secretvar = 1;</script>"
            b"<p>keepme</p>"
        )
        f = self._file("a.html", "text/html", payload)
        body = extract_text(f)
        self.assertIn("keepme", body)
        self.assertNotIn("secretvar", body)
        self.assertNotIn("color", body)

    def test_html_entities_are_decoded(self):
        f = self._file("a.html", "text/html", b"<p>caf&eacute; &amp; cr&egrave;me</p>")
        self.assertIn("café", extract_text(f))

    def test_binary_content_yields_nothing(self):
        f = self._file("a.png", "image/png", b"\x89PNG\r\n\x1a\n\xff\xfe")
        self.assertIsNone(extract_text(f))

    def test_undecodable_bytes_declared_as_text_yield_nothing(self):
        f = self._file("a.txt", "text/plain", b"\xff\xfe\xfd\xfc broken")
        self.assertIsNone(extract_text(f))

    def test_folder_yields_nothing(self):
        folder = File.objects.create(
            name="dir", node_type=File.NodeType.FOLDER, owner=self.user
        )
        self.assertIsNone(extract_text(folder))

    def test_missing_blob_yields_nothing(self):
        f = self._file("gone.md", "text/markdown", b"content")
        f.content.storage.delete(f.content.name)
        self.assertIsNone(extract_text(f))

    def test_missing_mime_yields_nothing(self):
        f = File.objects.create(
            name="mystery",
            node_type=File.NodeType.FILE,
            owner=self.user,
            content=ContentFile(b"text", name="mystery"),
        )
        self.assertIsNone(extract_text(f))

    def test_blank_content_yields_nothing(self):
        f = self._file("empty.md", "text/markdown", b"   \n\n  ")
        self.assertIsNone(extract_text(f))

    def test_body_is_capped(self):
        f = self._file("big.md", "text/markdown", b"a " * (BODY_CAP // 2 + 5_000))
        self.assertLessEqual(len(extract_text(f)), BODY_CAP)

    def test_a_cap_landing_mid_codepoint_does_not_lose_the_file(self):
        # Reading N bytes can split a multi-byte character; a strict decode
        # would fail and drop the whole document instead of the last char.
        payload = "é".encode() * (BODY_CAP * 2)
        f = self._file("accents.md", "text/markdown", payload)
        body = extract_text(f)
        self.assertTrue(body.startswith("é"))
        self.assertLessEqual(len(body), BODY_CAP)
