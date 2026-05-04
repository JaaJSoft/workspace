from django.test import TestCase

from workspace.chat.services.rendering import render_message_body


class RenderMessageBodyTest(TestCase):
    """Unit tests for markdown rendering."""

    def test_bold_and_italic(self):
        html = render_message_body("**bold** and *italic*")
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>italic</em>", html)

    def test_strikethrough(self):
        html = render_message_body("~~deleted~~")
        self.assertIn("<del>deleted</del>", html)

    def test_inline_code(self):
        html = render_message_body("use `print()`")
        self.assertIn('class="code-inline"', html)
        self.assertIn("print()", html)

    def test_fenced_code_block_with_language(self):
        html = render_message_body("```python\nprint('hi')\n```")
        self.assertIn('class="code-block"', html)
        self.assertIn('data-lang="python"', html)
        self.assertIn("<span", html)

    def test_fenced_code_block_without_language(self):
        html = render_message_body("```\nfoo = 1\n```")
        self.assertIn('class="code-block"', html)

    def test_unordered_list(self):
        html = render_message_body("- one\n- two\n- three")
        self.assertIn("<ul>", html)
        self.assertIn("<li>", html)

    def test_ordered_list(self):
        html = render_message_body("1. one\n2. two")
        self.assertIn("<ol>", html)

    def test_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        html = render_message_body(md)
        self.assertIn("<table>", html)
        self.assertIn("<th>", html)
        self.assertIn("<td>", html)

    def test_task_list(self):
        html = render_message_body("- [x] done\n- [ ] todo")
        self.assertIn('type="checkbox"', html)

    def test_blockquote(self):
        html = render_message_body("> quote")
        self.assertIn("<blockquote>", html)

    def test_autolink(self):
        html = render_message_body("Visit https://example.com")
        self.assertIn('<a href="https://example.com"', html)

    def test_xss_escaped(self):
        html = render_message_body('<script>alert("xss")</script>')
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_mentions_preserved(self):
        html = render_message_body("hello @alice", mention_map={"alice": 42})
        self.assertIn('class="mention-badge"', html)
        self.assertIn("@alice", html)
