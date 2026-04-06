from django.test import TestCase

from workspace.chat.services import (
    extract_mentions,
    render_message_body,
)


# ── render_message_body ─────────────────────────────────────────

class RenderMessageBodyTests(TestCase):

    def test_plain_text_renders_as_paragraph(self):
        html = render_message_body('Hello world')
        self.assertIn('Hello world', html)

    def test_markdown_bold(self):
        html = render_message_body('**bold**')
        self.assertIn('<strong>bold</strong>', html)

    def test_markdown_code_block(self):
        html = render_message_body('```python\nprint("hi")\n```')
        self.assertIn('code-block', html)
        self.assertIn('print', html)

    def test_markdown_inline_code(self):
        html = render_message_body('use `foo()` here')
        self.assertIn('code-inline', html)
        self.assertIn('foo()', html)

    def test_markdown_strikethrough(self):
        html = render_message_body('~~deleted~~')
        self.assertIn('<del>', html)

    def test_images_stripped(self):
        html = render_message_body('![alt text](http://example.com/img.png)')
        self.assertNotIn('<img', html)
        self.assertIn('alt text', html)

    def test_mention_without_map(self):
        html = render_message_body('@alice hello')
        # Without mention_map, @alice is just text
        self.assertNotIn('mention-badge', html)

    def test_mention_with_map(self):
        html = render_message_body('@alice hello', mention_map={'alice': 42})
        self.assertIn('mention-badge', html)
        self.assertIn('alice', html)
        self.assertIn('42', html)

    def test_mention_everyone(self):
        html = render_message_body('@everyone hello', mention_map={'_': None})
        self.assertIn('mention-everyone', html)
        self.assertIn('@everyone', html)

    def test_mention_unknown_user_not_highlighted(self):
        html = render_message_body('@unknown hello', mention_map={'alice': 1})
        self.assertNotIn('mention-badge', html)

    def test_mention_in_code_block_not_replaced(self):
        html = render_message_body('```\n@alice\n```', mention_map={'alice': 1})
        # @alice inside code should not get the badge
        # (it's in a code block, so placeholder won't be in code)
        self.assertIn('alice', html)


# ── extract_mentions ────────────────────────────────────────────

class ExtractMentionsTests(TestCase):

    def test_extracts_usernames(self):
        usernames, has_everyone = extract_mentions('Hello @alice and @bob')
        self.assertEqual(usernames, {'alice', 'bob'})
        self.assertFalse(has_everyone)

    def test_detects_everyone(self):
        usernames, has_everyone = extract_mentions('@everyone check this')
        self.assertTrue(has_everyone)
        self.assertNotIn('everyone', usernames)

    def test_no_mentions(self):
        usernames, has_everyone = extract_mentions('no mentions here')
        self.assertEqual(usernames, set())
        self.assertFalse(has_everyone)

    def test_duplicate_mentions(self):
        usernames, _ = extract_mentions('@alice @alice @bob')
        self.assertEqual(usernames, {'alice', 'bob'})

    def test_mention_with_everyone_and_users(self):
        usernames, has_everyone = extract_mentions('@everyone @alice')
        self.assertTrue(has_everyone)
        self.assertEqual(usernames, {'alice'})
