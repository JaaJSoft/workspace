import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from workspace.settings.env import available_cpus, env_non_negative_int

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


@patch("os.process_cpu_count", return_value=20)
class AvailableCpusTests(SimpleTestCase):
    """A cgroup filesystem is laid out in a temporary directory: the counts must
    not depend on the limits of the machine running the suite."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_follows_a_cgroup_v2_limit(self, _):
        """The reason this exists: a pod limited to 2 CPUs on a 20-core node."""
        self.write("cpu.max", "200000 100000\n")
        self.assertEqual(available_cpus(self.root), 2)

    def test_rounds_a_fractional_limit_up(self, _):
        self.write("cpu.max", "150000 100000\n")
        self.assertEqual(available_cpus(self.root), 2)

    def test_a_limit_below_one_cpu_still_gets_one(self, _):
        self.write("cpu.max", "50000 100000\n")
        self.assertEqual(available_cpus(self.root), 1)

    def test_cgroup_v2_without_a_limit_counts_the_cores(self, _):
        self.write("cpu.max", "max 100000\n")
        self.assertEqual(available_cpus(self.root), 20)

    def test_a_limit_above_the_cores_counts_the_cores(self, cpu_count):
        cpu_count.return_value = 4
        self.write("cpu.max", "800000 100000\n")
        self.assertEqual(available_cpus(self.root), 4)

    def test_follows_a_cgroup_v1_limit(self, _):
        self.write("cpu/cpu.cfs_quota_us", "300000\n")
        self.write("cpu/cpu.cfs_period_us", "100000\n")
        self.assertEqual(available_cpus(self.root), 3)

    def test_cgroup_v1_without_a_limit_counts_the_cores(self, _):
        self.write("cpu/cpu.cfs_quota_us", "-1\n")
        self.write("cpu/cpu.cfs_period_us", "100000\n")
        self.assertEqual(available_cpus(self.root), 20)

    def test_no_cgroup_counts_the_cores(self, _):
        self.assertEqual(available_cpus(self.root), 20)

    def test_an_unreadable_quota_counts_the_cores(self, _):
        """Settings are evaluated at import: a surprise here must not stop the
        worker from starting."""
        self.write("cpu.max", "lots\n")
        self.assertEqual(available_cpus(self.root), 20)

    def test_an_unknown_core_count_reads_as_one(self, cpu_count):
        cpu_count.return_value = None
        self.assertEqual(available_cpus(self.root), 1)
