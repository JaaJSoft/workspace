import os
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from workspace.settings.env import env_non_negative_int

PROBE = "WORKSPACE_TEST_PROBE_COUNT"


class EnvNonNegativeIntTests(SimpleTestCase):
    def test_reads_a_count(self):
        with patch.dict(os.environ, {PROBE: "2"}):
            self.assertEqual(env_non_negative_int(PROBE), 2)

    def test_zero_is_a_count(self):
        with patch.dict(os.environ, {PROBE: "0"}):
            self.assertEqual(env_non_negative_int(PROBE), 0)

    def test_unset_reads_as_none(self):
        self.assertIsNone(env_non_negative_int(PROBE))

    def test_blank_reads_as_none(self):
        with patch.dict(os.environ, {PROBE: "   "}):
            self.assertIsNone(env_non_negative_int(PROBE))

    def test_refuses_a_negative_count(self):
        """The dangerous value, and the reason this helper validates at all: a
        negative count does not fail, it means something else. Read as a proxy
        depth it makes DRF index X-Forwarded-For from the caller-controlled
        front of the list."""
        with patch.dict(os.environ, {PROBE: "-1"}):
            with self.assertRaises(ImproperlyConfigured):
                env_non_negative_int(PROBE)

    def test_refuses_a_non_numeric_value(self):
        with patch.dict(os.environ, {PROBE: "yes"}):
            with self.assertRaises(ImproperlyConfigured):
                env_non_negative_int(PROBE)

    def test_refuses_a_numeral_int_would_reject(self):
        """`isdigit()` is true for superscripts, `int()` is not."""
        with patch.dict(os.environ, {PROBE: "²"}):
            with self.assertRaises(ImproperlyConfigured):
                env_non_negative_int(PROBE)

    def test_names_the_variable_in_the_refusal(self):
        with patch.dict(os.environ, {PROBE: "yes"}):
            with self.assertRaisesMessage(ImproperlyConfigured, PROBE):
                env_non_negative_int(PROBE)
