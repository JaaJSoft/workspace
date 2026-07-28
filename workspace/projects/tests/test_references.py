from django.test import SimpleTestCase

from workspace.projects.services.references import (
    KEY_RE,
    generate_base_key,
    unique_project_key,
)


class GenerateBaseKeyTests(SimpleTestCase):
    def test_multi_word_uses_initials(self):
        self.assertEqual(generate_base_key("Website Redesign"), "WR")

    def test_initials_cap_at_five_words(self):
        self.assertEqual(generate_base_key("a b c d e f g"), "ABCDE")

    def test_single_word_uses_prefix(self):
        self.assertEqual(generate_base_key("Personal"), "PERS")

    def test_short_single_word_falls_back(self):
        self.assertEqual(generate_base_key("X"), "PROJ")

    def test_empty_and_symbol_names_fall_back(self):
        self.assertEqual(generate_base_key(""), "PROJ")
        self.assertEqual(generate_base_key("!!!"), "PROJ")

    def test_digit_only_name_falls_back(self):
        self.assertEqual(generate_base_key("42"), "PROJ")

    def test_leading_digit_initials_are_dropped(self):
        self.assertEqual(generate_base_key("2024 Roadmap Q3"), "RQ")

    def test_always_matches_the_key_format(self):
        for name in ("Website Redesign", "Personal", "x", "", "42", "éèç ûü"):
            self.assertRegex(generate_base_key(name), KEY_RE)


class UniqueProjectKeyTests(SimpleTestCase):
    def test_free_base_is_returned(self):
        self.assertEqual(unique_project_key("Website Redesign", taken=set()), "WR")

    def test_collision_appends_suffix(self):
        self.assertEqual(unique_project_key("Website Redesign", taken={"WR"}), "WR2")
        self.assertEqual(
            unique_project_key("Website Redesign", taken={"WR", "WR2"}), "WR3"
        )

    def test_double_digit_suffix(self):
        taken = {"WR"} | {f"WR{i}" for i in range(2, 12)}
        self.assertEqual(unique_project_key("Website Redesign", taken=taken), "WR12")
