from django.test import SimpleTestCase

from workspace.common.limits import clamp_limit


class ClampLimitTests(SimpleTestCase):
    def test_valid_value_passes_through(self):
        self.assertEqual(clamp_limit(7), 7)
        self.assertEqual(clamp_limit("7"), 7)

    def test_missing_or_garbage_falls_back_to_the_default(self):
        for value in (None, "", "abc", [], object()):
            self.assertEqual(clamp_limit(value, default=10), 10)

    def test_value_is_clamped_to_the_allowed_range(self):
        self.assertEqual(clamp_limit(0), 1)
        self.assertEqual(clamp_limit(-5), 1)
        self.assertEqual(clamp_limit(9999, maximum=50), 50)

    def test_default_itself_is_clamped(self):
        self.assertEqual(clamp_limit("abc", default=99, maximum=5), 5)
