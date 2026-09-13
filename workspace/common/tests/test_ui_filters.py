from django.test import SimpleTestCase

from workspace.common.templatetags.ui_filters import compact_number


class CompactNumberTests(SimpleTestCase):
    def test_small_values_are_left_alone(self):
        self.assertEqual(compact_number(0), "0")
        self.assertEqual(compact_number(999), "999")

    def test_thousands_keep_one_decimal_below_ten(self):
        self.assertEqual(compact_number(1200), "1.2k")
        self.assertEqual(compact_number(1000), "1k")
        self.assertEqual(compact_number(9949), "9.9k")
        self.assertEqual(compact_number(9960), "10k")

    def test_thousands_drop_the_decimal_from_ten_up(self):
        self.assertEqual(compact_number(12345), "12k")
        self.assertEqual(compact_number(128000), "128k")

    def test_millions(self):
        self.assertEqual(compact_number(1_500_000), "1.5M")
        self.assertEqual(compact_number(12_000_000), "12M")

    def test_a_value_that_would_print_as_1000k_is_promoted(self):
        self.assertEqual(compact_number(999_499), "999k")
        self.assertEqual(compact_number(999_500), "1M")
        self.assertEqual(compact_number(999_999), "1M")

    def test_non_numbers_render_empty(self):
        self.assertEqual(compact_number(None), "")
        self.assertEqual(compact_number("abc"), "")
