from django.test import SimpleTestCase

from workspace.projects.services.references import (
    KEY_MAX_LENGTH,
    KEY_RE,
    generate_base_key,
    personal_key_base,
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


class PersonalKeyBaseTests(SimpleTestCase):
    def test_dotted_username_uses_initials(self):
        self.assertEqual(personal_key_base("pierre.chopinet"), "PERSPC")

    def test_single_word_username_uses_prefix(self):
        self.assertEqual(personal_key_base("pierre"), "PERSPIER")

    def test_separators_split_words(self):
        self.assertEqual(personal_key_base("jean-luc_picard"), "PERSJLP")

    def test_initials_cap_at_five_words(self):
        self.assertEqual(personal_key_base("a b c d e f g"), "PERSABCDE")

    def test_result_always_fits_the_key_column(self):
        for username in ("a b c d e f g", "pierre.chopinet", "verylongusername"):
            self.assertLessEqual(len(personal_key_base(username)), KEY_MAX_LENGTH)

    def test_accents_split_words_and_keep_the_ascii_run(self):
        self.assertEqual(personal_key_base("éric"), "PERSRIC")

    def test_usernames_without_ascii_keep_the_bare_prefix(self):
        self.assertEqual(personal_key_base(""), "PERS")
        self.assertEqual(personal_key_base("éèç"), "PERS")

    def test_digit_username_stays_valid(self):
        # The prefix supplies the leading letter KEY_RE demands.
        self.assertEqual(personal_key_base("42"), "PERS42")

    def test_always_matches_the_key_format(self):
        for username in ("pierre.chopinet", "x", "", "42", "éèç", "a b c d e f g"):
            self.assertRegex(personal_key_base(username), KEY_RE)


class UniqueProjectKeyTests(SimpleTestCase):
    def test_free_base_is_returned(self):
        self.assertEqual(unique_project_key("WR", taken=set()), "WR")

    def test_collision_appends_suffix(self):
        self.assertEqual(unique_project_key("WR", taken={"WR"}), "WR2")
        self.assertEqual(unique_project_key("WR", taken={"WR", "WR2"}), "WR3")

    def test_double_digit_suffix(self):
        taken = {"WR"} | {f"WR{i}" for i in range(2, 12)}
        self.assertEqual(unique_project_key("WR", taken=taken), "WR12")

    def test_max_length_base_is_truncated_to_fit_the_suffix(self):
        self.assertEqual(
            unique_project_key("PERSPIERRE", taken={"PERSPIERRE"}), "PERSPIERR2"
        )
