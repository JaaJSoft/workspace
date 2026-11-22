from django.test import SimpleTestCase

from workspace.ai.services.reply_text import split_inline_thinking, truncate_middle


class SplitInlineThinkingTests(SimpleTestCase):
    def test_no_think_block_returns_empty_thinking(self):
        thinking, cleaned = split_inline_thinking("just a reply")
        self.assertEqual(thinking, "")
        self.assertEqual(cleaned, "just a reply")

    def test_think_block_is_captured_and_stripped(self):
        thinking, cleaned = split_inline_thinking(
            "<think>plan the answer</think>Hello there"
        )
        self.assertEqual(thinking, "plan the answer")
        self.assertEqual(cleaned, "Hello there")

    def test_multiple_blocks_join_with_blank_line(self):
        thinking, cleaned = split_inline_thinking(
            "<think>first</think>mid<think>second</think>end"
        )
        self.assertEqual(thinking, "first\n\nsecond")
        self.assertEqual(cleaned, "midend")

    def test_unclosed_tag_captures_nothing_and_keeps_content(self):
        content = "<think>never closed... Hello"
        thinking, cleaned = split_inline_thinking(content)
        self.assertEqual(thinking, "")
        self.assertEqual(cleaned, content)

    def test_case_insensitive(self):
        thinking, cleaned = split_inline_thinking("<THINK>loud</THINK>hi")
        self.assertEqual(thinking, "loud")
        self.assertEqual(cleaned, "hi")

    def test_tag_spelling_variants_are_captured(self):
        for tag in ("thought", "thoughts", "thinking", "reasoning"):
            with self.subTest(tag=tag):
                thinking, cleaned = split_inline_thinking(
                    f"<{tag}>weighing options</{tag}>Hello there"
                )
                self.assertEqual(thinking, "weighing options")
                self.assertEqual(cleaned, "Hello there")

    def test_mismatched_open_and_close_tags_are_left_alone(self):
        content = "<think>oops</thought>Hello"
        thinking, cleaned = split_inline_thinking(content)
        self.assertEqual(thinking, "")
        self.assertEqual(cleaned, content)

    def test_mismatched_block_does_not_swallow_the_next_one(self):
        thinking, cleaned = split_inline_thinking(
            "<think>bad</thought><think>real</think>Answer"
        )
        self.assertEqual(thinking, "real")
        self.assertEqual(cleaned, "<think>bad</thought>Answer")


class TruncateMiddleTests(SimpleTestCase):
    def test_short_text_is_untouched(self):
        self.assertEqual(truncate_middle("hello", 100), "hello")

    def test_head_and_tail_survive_the_cut(self):
        text = "START" + ("x" * 5000) + "CONCLUSION"
        out = truncate_middle(text, 500)
        self.assertLessEqual(len(out), 500)
        self.assertTrue(out.startswith("START"))
        self.assertTrue(out.endswith("CONCLUSION"))
        self.assertIn("characters omitted from the middle", out)

    def test_hint_names_the_call_in_the_residue(self):
        out = truncate_middle(
            "y" * 5000, 500, hint="fetch_url(https://example.com/doc)"
        )
        self.assertIn("of fetch_url(https://example.com/doc)", out)
        self.assertLessEqual(len(out), 500)

    def test_budget_too_small_for_a_marker_falls_back_to_a_hard_cut(self):
        out = truncate_middle("z" * 500, 20)
        self.assertEqual(out, "z" * 20)
