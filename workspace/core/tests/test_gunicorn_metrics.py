"""A real multi-worker gunicorn serves /metrics totals covering every worker."""

import base64
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from prometheus_client.parser import text_string_to_metric_families

WORKERS = 2
REQUESTS = 60
AUTH = "Basic " + base64.b64encode(b"prom:s3cret").decode()
LIVE_RESPONSES = {"status": "200", "view": "health-live", "method": "GET"}


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _worker_pids(master_pid):
    pids = set()
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        except OSError:
            continue
        if int(fields[1]) == master_pid:
            pids.add(int(stat.parent.name))
    return pids


@unittest.skipIf(sys.platform == "win32", "gunicorn needs a POSIX host")
class GunicornWorkersMetricsTests(SimpleTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base_url = f"http://127.0.0.1:{_free_port()}"
        env = {k: v for k, v in os.environ.items() if k != "PROMETHEUS_MULTIPROC_DIR"}
        env |= {
            "DATABASE_URL": f"sqlite:///{tmp.name}/db.sqlite3",
            "METRICS_USER": "prom",
            "METRICS_PASSWORD": "s3cret",
        }
        self.log = open(Path(tmp.name) / "gunicorn.log", "w+", encoding="utf-8")  # noqa: SIM115
        self.addCleanup(self.log.close)
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gunicorn",
                "workspace.wsgi:application",
                "-c",
                "gunicorn.conf.py",
                "-b",
                self.base_url.removeprefix("http://"),
                "-w",
                str(WORKERS),
            ],
            cwd=settings.BASE_DIR,
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        self.addCleanup(self._stop)
        self._wait_for(lambda: len(_worker_pids(self.proc.pid)) == WORKERS)
        self._wait_for(self._serves)

    def _stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()

    def _wait_for(self, condition, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                break
            if condition():
                return
            time.sleep(0.2)
        self.log.seek(0)
        self.fail(f"gunicorn never got ready:\n{self.log.read()}")

    def _serves(self):
        # Probes go to /metrics unauthenticated so they never count as health-live.
        try:
            self._get("/metrics")
        except urllib.error.HTTPError as exc:
            return exc.code == 401
        except OSError:
            return False
        return False

    def _get(self, path, headers=None):
        request = urllib.request.Request(self.base_url + path, headers=headers or {})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read().decode()

    def _live_responses(self):
        for family in text_string_to_metric_families(
            self._get("/metrics", {"Authorization": AUTH})
        ):
            for sample in family.samples:
                if (
                    sample.name
                    == "django_http_responses_total_by_status_view_method_total"
                    and sample.labels == LIVE_RESPONSES
                ):
                    return sample.value
        return 0.0

    def test_every_scrape_counts_the_requests_of_every_worker(self):
        before = self._live_responses()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self._get("/health/live"), range(REQUESTS)))

        scrapes = [self._live_responses() - before for _ in range(2 * WORKERS)]
        self.assertEqual(scrapes, [REQUESTS] * len(scrapes))

        victim = min(_worker_pids(self.proc.pid))
        os.kill(victim, signal.SIGKILL)
        self._wait_for(lambda: len(_worker_pids(self.proc.pid) - {victim}) == WORKERS)
        self._wait_for(self._serves)
        after_kill = [self._live_responses() - before for _ in range(2 * WORKERS)]
        self.assertEqual(after_kill, [REQUESTS] * len(after_kill))
