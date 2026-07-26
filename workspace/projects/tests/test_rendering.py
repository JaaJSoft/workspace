from django.test import SimpleTestCase

from workspace.projects.services.rendering import render_task_description


class RenderTaskDescriptionTests(SimpleTestCase):
    def test_renders_basic_markdown(self):
        html = render_task_description("**bold** and *italic*")
        self.assertIn("<strong>bold</strong>", html)
        self.assertIn("<em>italic</em>", html)

    def test_escapes_raw_html(self):
        html = render_task_description('<script>alert("x")</script>')
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_renders_task_lists(self):
        html = render_task_description("- [x] done\n- [ ] todo")
        self.assertIn("<li", html)

    def test_empty_and_none_return_empty_string(self):
        self.assertEqual(render_task_description(""), "")
        self.assertEqual(render_task_description(None), "")
