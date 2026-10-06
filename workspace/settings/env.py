"""Helpers reading the environment the settings are evaluated in: environment
variables, and the CPUs the process may use.

Importing this module loads the ``.env`` file, so it must be the first thing
any settings submodule touches before reading ``os.getenv``.
"""

import math
import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

# Values accepted as "true" in every boolean env var of the project.
_TRUE_VALUES = {"1", "true", "yes", "on"}

_CGROUP_ROOT = Path("/sys/fs/cgroup")


def env_bool(name, default=False):
    """Read a boolean env var. Anything outside _TRUE_VALUES reads as False."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).lower() in _TRUE_VALUES


def env_list(name):
    """Read a comma-separated env var into a list of stripped, non-empty items."""
    raw = os.getenv(name)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def env_non_negative_int(name):
    """Read a non-negative integer env var, or ``None`` when it is unset.

    Refuses loudly where the project's other numeric settings simply let
    ``int()`` raise, because the settings read through here have to fail
    closed. A negative value is the reason: it does not fail at all, it
    quietly means something else.
    """
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    # isdigit() alone accepts superscripts that int() then rejects.
    if not (raw.isascii() and raw.isdigit()):
        raise ImproperlyConfigured(
            f"{name} must be a non-negative integer, got {raw!r}"
        )
    return int(raw)


def available_cpus(cgroup_root=_CGROUP_ROOT):
    """How many CPUs this process may keep busy, a container CPU limit included.

    os.process_cpu_count() only follows the affinity mask. A container's CPU
    limit (Kubernetes ``resources.limits.cpu``, ``docker run --cpus``) is a
    quota instead: the kernel throttles the container once it has used its
    share, and every core of the node stays visible.
    """
    cpus = os.process_cpu_count() or 1
    quota = _cpu_quota(cgroup_root)
    if quota is None:
        return cpus
    return max(1, min(cpus, math.ceil(quota)))


def _cpu_quota(root):
    """The CPU quota of the cgroup at *root*, in CPUs, or None when unlimited.

    Inside a container the root of the cgroup filesystem is the container's own
    cgroup. cgroup v2 writes "<quota> <period>" to cpu.max, "max" standing for
    no limit; v1 splits the pair across two files, -1 standing for no limit.
    """
    try:
        quota, period = (root / "cpu.max").read_text(encoding="utf-8").split()
    except OSError, ValueError:
        # The v1 controller's directory is named after whatever it is mounted
        # with (cpu, cpu,cpuacct...), and only it holds these two files.
        controller = next(
            (path.parent for path in root.glob("*/cpu.cfs_quota_us")), None
        )
        if controller is None:
            return None
        try:
            quota = (controller / "cpu.cfs_quota_us").read_text(encoding="utf-8")
            period = (controller / "cpu.cfs_period_us").read_text(encoding="utf-8")
        except OSError:
            return None
    try:
        quota, period = int(quota), int(period)
    except ValueError:
        return None
    if quota <= 0 or period <= 0:
        return None
    return quota / period
