"""Hooks in gunicorn.conf.py that keep Prometheus metrics coherent across workers."""

import os
import runpy
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

CONF_PATH = Path(settings.BASE_DIR) / "gunicorn.conf.py"
ENV = "PROMETHEUS_MULTIPROC_DIR"


class GunicornConfTests(SimpleTestCase):
    def setUp(self):
        self.conf = runpy.run_path(str(CONF_PATH))
        self.server = SimpleNamespace()
        env = patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(ENV, None)

    def _operator_dir(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_without_a_directory_one_is_created_for_the_workers(self):
        self.conf["on_starting"](self.server)
        path = Path(os.environ[ENV])
        self.addCleanup(self.conf["on_exit"], self.server)
        self.assertTrue(path.is_dir())
        self.assertEqual(list(path.iterdir()), [])

    def test_the_created_directory_is_removed_on_exit(self):
        self.conf["on_starting"](self.server)
        path = Path(os.environ[ENV])
        self.conf["on_exit"](self.server)
        self.assertFalse(path.exists())

    def test_an_operator_directory_is_emptied_of_a_previous_run(self):
        path = self._operator_dir()
        (path / "counter_7.db").write_bytes(b"stale")
        (path / "notes.txt").write_text("not ours", encoding="utf-8")
        os.environ[ENV] = str(path)

        self.conf["on_starting"](self.server)

        self.assertEqual(os.environ[ENV], str(path))
        self.assertEqual(sorted(p.name for p in path.iterdir()), ["notes.txt"])

    def test_an_operator_directory_survives_exit(self):
        path = self._operator_dir()
        os.environ[ENV] = str(path)
        self.conf["on_starting"](self.server)
        self.conf["on_exit"](self.server)
        self.assertTrue(path.is_dir())

    def test_a_dead_worker_keeps_its_counters_and_loses_its_live_gauges(self):
        path = self._operator_dir()
        os.environ[ENV] = str(path)
        (path / "counter_42.db").write_bytes(b"")
        (path / "gauge_livesum_42.db").write_bytes(b"")
        (path / "gauge_livesum_43.db").write_bytes(b"")

        self.conf["child_exit"](self.server, SimpleNamespace(pid=42))

        self.assertEqual(
            sorted(p.name for p in path.iterdir()),
            ["counter_42.db", "gauge_livesum_43.db"],
        )

    def test_loading_the_config_leaves_prometheus_client_unimported(self):
        # The master imports this file before forking; importing prometheus_client
        # there would fix in-memory storage for every worker.
        probe = (
            "import runpy, sys; runpy.run_path(sys.argv[1]); "
            "print('prometheus_client' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-c", probe, str(CONF_PATH)],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(out.stdout.strip(), "False")
