from django.template import Context, Template, TemplateSyntaxError
from django.test import TestCase
from django.utils.safestring import SafeString, mark_safe


def _render(template_string, context=None):
    """Render a template string and return the output."""
    tpl = Template("{% load ui_tags %}" + template_string)
    return tpl.render(Context(context or {}))


def _parse_items(template_body, context=None):
    """Render a {% help_items as items %}…{% endhelp_items %} block and
    return the parsed items list from context."""
    ctx = Context(context or {})
    tpl = Template(
        "{% load ui_tags %}"
        "{% help_items as items %}" + template_body + "{% endhelp_items %}"
    )
    tpl.render(ctx)
    return ctx["items"]


class HelpItemsTagParsingTests(TestCase):
    def test_single_item_basic(self):
        items = _parse_items("keyboard | Shortcuts\n<p>content</p>")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["icon"], "keyboard")
        self.assertEqual(items[0]["title"], "Shortcuts")
        self.assertFalse(items[0]["checked"])
        self.assertIn("<p>content</p>", items[0]["content"])

    def test_multiple_items_separated_by_dashes(self):
        body = (
            "keyboard | Shortcuts\n<p>A</p>\n"
            "---\n"
            "mail | Mail\n<p>B</p>"
        )
        items = _parse_items(body)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["icon"], "keyboard")
        self.assertEqual(items[1]["icon"], "mail")

    def test_checked_flag_keyword(self):
        items = _parse_items("keyboard | Shortcuts | checked\n<p>x</p>")
        self.assertTrue(items[0]["checked"])

    def test_checked_flag_true(self):
        items = _parse_items("keyboard | Shortcuts | true\n<p>x</p>")
        self.assertTrue(items[0]["checked"])

    def test_checked_flag_one(self):
        items = _parse_items("keyboard | Shortcuts | 1\n<p>x</p>")
        self.assertTrue(items[0]["checked"])

    def test_checked_flag_yes(self):
        items = _parse_items("keyboard | Shortcuts | yes\n<p>x</p>")
        self.assertTrue(items[0]["checked"])

    def test_unchecked_when_flag_absent(self):
        items = _parse_items("keyboard | Shortcuts\n<p>x</p>")
        self.assertFalse(items[0]["checked"])

    def test_unchecked_when_flag_empty(self):
        items = _parse_items("keyboard | Shortcuts | \n<p>x</p>")
        self.assertFalse(items[0]["checked"])

    def test_empty_blocks_are_skipped(self):
        body = "---\nkeyboard | Shortcuts\n<p>x</p>\n---\n"
        items = _parse_items(body)
        self.assertEqual(len(items), 1)

    def test_whitespace_around_separator_is_handled(self):
        body = "keyboard | A\n<p>x</p>\n  ---  \nmail | B\n<p>y</p>"
        items = _parse_items(body)
        self.assertEqual(len(items), 2)

    def test_content_is_marked_safe(self):
        items = _parse_items("keyboard | Shortcuts\n<p>content</p>")
        self.assertIsInstance(items[0]["content"], SafeString)

    def test_icon_and_title_are_stripped(self):
        items = _parse_items("  keyboard  |  My Title  \n<p>x</p>")
        self.assertEqual(items[0]["icon"], "keyboard")
        self.assertEqual(items[0]["title"], "My Title")

    def test_multiline_content_preserved(self):
        body = "keyboard | Shortcuts\n<h4>Nav</h4>\n<table>\n<tr></tr>\n</table>"
        items = _parse_items(body)
        content = items[0]["content"]
        self.assertIn("<h4>Nav</h4>", content)
        self.assertIn("<table>", content)
        self.assertIn("<tr></tr>", content)

    def test_django_template_tags_rendered_in_content(self):
        body = "keyboard | Shortcuts\n<p>{% if flag %}yes{% else %}no{% endif %}</p>"
        items = _parse_items(body, context={"flag": True})
        self.assertIn("yes", items[0]["content"])
        self.assertNotIn("{% if", items[0]["content"])

    def test_tag_produces_no_output(self):
        output = _render("{% help_items as items %}keyboard | Title\n<p>x</p>{% endhelp_items %}")
        self.assertEqual(output.strip(), "")

    def test_invalid_syntax_raises_error(self):
        with self.assertRaises(TemplateSyntaxError):
            Template("{% load ui_tags %}{% help_items %}keyboard | Title{% endhelp_items %}")

    def test_invalid_syntax_missing_as_raises_error(self):
        with self.assertRaises(TemplateSyntaxError):
            Template("{% load ui_tags %}{% help_items items %}keyboard | Title{% endhelp_items %}")


class HelpDialogItemTemplateTests(TestCase):
    def _render_item(self, icon, title, checked, content, accent_color="primary"):
        item = {"icon": icon, "title": title, "checked": checked, "content": mark_safe(content)}
        tpl = Template(
            '{% include "ui/partials/help_dialog_item.html" %}'
        )
        return tpl.render(Context({"item": item, "accent_color": accent_color}))

    def test_renders_icon(self):
        html = self._render_item("keyboard", "Shortcuts", False, "<p>x</p>")
        self.assertIn('data-lucide="keyboard"', html)

    def test_renders_title(self):
        html = self._render_item("keyboard", "Shortcuts", False, "<p>x</p>")
        self.assertIn("Shortcuts", html)

    def test_renders_content(self):
        html = self._render_item("keyboard", "Shortcuts", False, "<p>my content</p>")
        self.assertIn("<p>my content</p>", html)

    def test_checked_item_has_checked_attribute(self):
        html = self._render_item("keyboard", "Shortcuts", True, "<p>x</p>")
        self.assertIn('checked="checked"', html)

    def test_unchecked_item_has_no_checked_attribute(self):
        html = self._render_item("keyboard", "Shortcuts", False, "<p>x</p>")
        self.assertNotIn('checked="checked"', html)

    def test_accent_color_applied(self):
        html = self._render_item("keyboard", "Shortcuts", False, "<p>x</p>", accent_color="success")
        self.assertIn("text-success", html)


class HelpDialogTemplateTests(TestCase):
    def _render_dialog(self, dialog_id, accent_color, items):
        tpl = Template('{% include "ui/partials/help_dialog.html" %}')
        return tpl.render(Context({
            "dialog_id": dialog_id,
            "accent_color": accent_color,
            "items": items,
        }))

    def test_renders_dialog_id(self):
        html = self._render_dialog("my-dialog", "primary", [])
        self.assertIn('id="my-dialog"', html)

    def test_renders_accent_color_in_header(self):
        html = self._render_dialog("my-dialog", "success", [])
        self.assertIn("text-success", html)

    def test_renders_all_items(self):
        items = [
            {"icon": "keyboard", "title": "Shortcuts", "checked": True, "content": mark_safe("<p>A</p>")},
            {"icon": "mail", "title": "Mail", "checked": False, "content": mark_safe("<p>B</p>")},
        ]
        html = self._render_dialog("my-dialog", "primary", items)
        self.assertIn("Shortcuts", html)
        self.assertIn("Mail", html)
        self.assertIn("<p>A</p>", html)
        self.assertIn("<p>B</p>", html)

    def test_empty_items_renders_valid_dialog(self):
        html = self._render_dialog("my-dialog", "primary", [])
        self.assertIn('<dialog id="my-dialog"', html)
