from django.test import SimpleTestCase

from workspace.common.text import caseless, fold_text


class FoldTextTests(SimpleTestCase):
    def test_strips_accents_and_case(self):
        self.assertEqual(fold_text("Hélène Çédille"), "helene cedille")

    def test_an_accented_and_a_plain_spelling_fold_alike(self):
        self.assertEqual(fold_text("JOSÉ"), fold_text("jose"))

    def test_splits_compatibility_forms_and_casefolds(self):
        self.assertEqual(fold_text("ﬁnal Straße"), "final strasse")

    def test_leaves_letters_without_a_decomposition(self):
        self.assertEqual(fold_text("Øystein Œuvre"), "øystein œuvre")

    def test_none_is_empty(self):
        self.assertEqual(fold_text(None), "")


class CaselessTests(SimpleTestCase):
    def test_case_variants_fold_alike(self):
        self.assertEqual(caseless("Été.TXT"), caseless("été.txt"))

    def test_composed_and_decomposed_accents_fold_alike(self):
        self.assertEqual(caseless("é.txt"), caseless("é.txt"))

    def test_accents_still_count(self):
        self.assertNotEqual(caseless("é.txt"), caseless("e.txt"))

    def test_folds_beyond_ascii(self):
        self.assertEqual(caseless("ΣΟΦΙΑ Straße"), caseless("σοφια strasse"))
