"""Gunicorn hooks for the container image.

Binding, worker count and logging stay on the Dockerfile command line; this
file carries what flags cannot express.

Every worker is its own process with its own Prometheus values, so a scrape
answered by one worker would only report that worker's share. In
prometheus_client's multiprocess mode each worker writes its values to per-pid
files in PROMETHEUS_MULTIPROC_DIR and /metrics sums them.

prometheus_client picks its storage once, when first imported, from that
variable. Nothing here may import it at module level: the master would settle
on in-memory storage and every forked worker would inherit that choice.
"""

import glob
import os
import shutil
import tempfile

MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"

_created_dir = None


def on_starting(server):
    """Give the workers an empty metrics directory before any of them forks."""
    global _created_dir
    path = os.environ.get(MULTIPROC_ENV)
    if path:
        # Files left by a previous run would be added to this run's totals.
        os.makedirs(path, exist_ok=True)
        for stale in glob.glob(os.path.join(path, "*.db")):
            os.remove(stale)
    else:
        _created_dir = tempfile.mkdtemp(prefix="prometheus-")
        os.environ[MULTIPROC_ENV] = _created_dir


def child_exit(server, worker):
    # A dead worker's counters stay in the totals; only its live gauges go.
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(worker.pid)


def on_exit(server):
    if _created_dir:
        shutil.rmtree(_created_dir, ignore_errors=True)
