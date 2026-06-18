from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase

from workspace.files.models import File
from workspace.files.services.excerpt import first_content_line, first_line_from_text

User = get_user_model()


class FirstLineFromTextTests(SimpleTestCase):
    def test_returns_first_real_line(self):
        self.assertEqual(first_line_from_text("Hello world\nSecond"), "Hello world")

    def test_skips_leading_blank_lines(self):
        self.assertEqual(first_line_from_text("\n\n   \nActual text"), "Actual text")

    def test_skips_markdown_heading(self):
        self.assertEqual(first_line_from_text("# Title\n\nBody line"), "Body line")

    def test_skips_multiple_headings(self):
        self.assertEqual(first_line_from_text("# A\n## B\nReal body"), "Real body")

    def test_skips_yaml_frontmatter(self):
        text = "---\ntitle: X\ntags: [a, b]\n---\nThe body"
        self.assertEqual(first_line_from_text(text), "The body")

    def test_strips_basic_markdown_markers(self):
        self.assertEqual(first_line_from_text("- **bold** item"), "bold item")

    def test_truncates_to_max_len(self):
        out = first_line_from_text("x" * 300, max_len=160)
        self.assertEqual(len(out), 160)

    def test_empty_returns_empty(self):
        self.assertEqual(first_line_from_text(""), "")

    def test_none_returns_empty(self):
        self.assertEqual(first_line_from_text(None), "")

    def test_only_headings_returns_empty(self):
        self.assertEqual(first_line_from_text("# A\n## B"), "")

    def test_strips_bold_paired(self):
        self.assertEqual(first_line_from_text("**bold** text"), "bold text")

    def test_strips_italic_paired(self):
        self.assertEqual(first_line_from_text("*italic* word"), "italic word")

    def test_strips_code_paired(self):
        self.assertEqual(first_line_from_text("`code` here"), "code here")

    def test_preserves_intraword_underscore(self):
        self.assertEqual(
            first_line_from_text("profit_margin is 50%"), "profit_margin is 50%"
        )


class FirstContentLineTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")

    def test_folder_returns_empty(self):
        folder = File.objects.create(
            name="dir", node_type=File.NodeType.FOLDER, owner=self.user
        )
        self.assertEqual(first_content_line(folder), "")

    def test_reads_markdown_first_line(self):
        note = File.objects.create(
            name="note.md",
            node_type=File.NodeType.FILE,
            type="markdown",
            owner=self.user,
            content=ContentFile(b"# Title\n\nHello there", name="note.md"),
        )
        self.assertEqual(first_content_line(note), "Hello there")
