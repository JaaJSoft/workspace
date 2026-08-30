from django.test import SimpleTestCase

from workspace.common.documents.budget import TextBudget


class TextBudgetTests(SimpleTestCase):
    def test_collects_until_the_ceiling(self):
        budget = TextBudget(10)
        budget.add("abcde")
        self.assertFalse(budget.full)
        budget.add("fghij")
        self.assertTrue(budget.full)
        self.assertEqual(budget.text(), "abcdefghij")

    def test_the_chunk_that_overflows_is_truncated_not_dropped(self):
        budget = TextBudget(5)
        budget.add("abc")
        budget.add("defgh")
        self.assertEqual(budget.text(), "abcde")

    def test_additions_past_the_ceiling_are_ignored(self):
        budget = TextBudget(3)
        budget.add("abcdef")
        budget.add("ghi")
        self.assertEqual(budget.text(), "abc")

    def test_the_separator_only_goes_between(self):
        budget = TextBudget(100)
        budget.add("first", separator="\n")
        budget.add("second", separator="\n")
        self.assertEqual(budget.text(), "first\nsecond")

    def test_empty_additions_do_not_earn_a_separator(self):
        budget = TextBudget(100)
        budget.add("", separator="\n")
        budget.add("only", separator="\n")
        self.assertEqual(budget.text(), "only")

    def test_a_zero_ceiling_is_full_from_the_start(self):
        budget = TextBudget(0)
        self.assertTrue(budget.full)
        budget.add("anything")
        self.assertEqual(budget.text(), "")
