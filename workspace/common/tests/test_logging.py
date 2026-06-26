from django.test import SimpleTestCase

from workspace.common.logging import scrub


class ScrubTests(SimpleTestCase):
    def test_strips_newline(self):
        self.assertEqual(scrub("name\nforged"), "nameforged")

    def test_strips_carriage_return(self):
        self.assertEqual(scrub("name\rforged"), "nameforged")

    def test_strips_crlf_sequence(self):
        self.assertEqual(
            scrub("name\r\nINFO forged log line"), "nameINFO forged log line"
        )

    def test_strips_multiple_occurrences(self):
        self.assertEqual(scrub("a\nb\r\nc\rd"), "abcd")

    def test_leaves_clean_string_untouched(self):
        self.assertEqual(scrub("plain value"), "plain value")

    def test_preserves_other_whitespace_and_unicode(self):
        # Only CR and LF are stripped; tabs, spaces and unicode survive.
        self.assertEqual(scrub("a\tb cé "), "a\tb cé ")

    def test_coerces_non_string_to_str(self):
        self.assertEqual(scrub(42), "42")
        self.assertEqual(scrub(None), "None")

    def test_coerces_object_with_newline_in_repr(self):
        class Weird:
            def __str__(self):
                return "line1\nline2"

        self.assertEqual(scrub(Weird()), "line1line2")

    def test_returns_str_for_non_string_input(self):
        self.assertIsInstance(scrub(3.14), str)
