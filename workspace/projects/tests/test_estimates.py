from decimal import Decimal

from django.test import SimpleTestCase

from workspace.projects.services.estimates import format_estimate


class FormatEstimateTests(SimpleTestCase):
    def test_none_is_empty(self):
        self.assertEqual(format_estimate(None), "")

    def test_whole_values_drop_the_decimal(self):
        self.assertEqual(format_estimate(Decimal("3.0")), "3")
        self.assertEqual(format_estimate(Decimal("0.0")), "0")
        self.assertEqual(format_estimate(Decimal("40")), "40")

    def test_fractional_values_keep_one_decimal(self):
        self.assertEqual(format_estimate(Decimal("3.5")), "3.5")
        self.assertEqual(format_estimate(Decimal("0.5")), "0.5")
