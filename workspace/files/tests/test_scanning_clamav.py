import io
import socket
import tempfile
import types
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch

from clamdpy import sockets as clamd_sockets
from django.test import SimpleTestCase, override_settings

from workspace.files.models import FileScan
from workspace.files.services.scanning.clamav import ClamAVScanner
from workspace.files.tests.scanning_fake_daemon import FakeClamd, free_port


def _tcp(daemon, timeout=5.0):
    return override_settings(
        FILES_CLAMAV_SOCKET="",
        FILES_CLAMAV_HOST=daemon.host,
        FILES_CLAMAV_PORT=daemon.port,
        FILES_CLAMAV_TIMEOUT=timeout,
    )


class ClamAVScannerTests(SimpleTestCase):
    def test_ok_reply_is_clean(self):
        with FakeClamd(reply=b"stream: OK\n") as daemon, _tcp(daemon):
            verdict = ClamAVScanner().scan(io.BytesIO(b"harmless"), name="a.txt")
        self.assertEqual(verdict.status, FileScan.Status.CLEAN)
        self.assertEqual(verdict.signature, "")

    def test_found_reply_is_infected_and_carries_the_signature(self):
        with (
            FakeClamd(reply=b"stream: Unit.Test.Signature FOUND\n") as daemon,
            _tcp(daemon),
        ):
            verdict = ClamAVScanner().scan(io.BytesIO(b"payload"), name="a.bin")
        self.assertEqual(verdict.status, FileScan.Status.INFECTED)
        self.assertEqual(verdict.signature, "Unit.Test.Signature")

    def test_error_reply_is_an_error_verdict(self):
        with FakeClamd(reply=b"stream: Broken pipe ERROR\n") as daemon, _tcp(daemon):
            verdict = ClamAVScanner().scan(io.BytesIO(b"x"), name="a.bin")
        self.assertEqual(verdict.status, FileScan.Status.ERROR)
        self.assertIn("Broken pipe", verdict.detail)

    def test_daemon_size_limit_is_skipped_not_error(self):
        reply = b"INSTREAM size limit exceeded. ERROR\n"
        with FakeClamd(reply=reply) as daemon, _tcp(daemon):
            verdict = ClamAVScanner().scan(io.BytesIO(b"x" * 32), name="big.bin")
        self.assertEqual(verdict.status, FileScan.Status.SKIPPED)

    def test_unreachable_daemon_is_an_error_verdict(self):
        port = free_port()
        with override_settings(
            FILES_CLAMAV_SOCKET="",
            FILES_CLAMAV_HOST="127.0.0.1",
            FILES_CLAMAV_PORT=port,
            FILES_CLAMAV_TIMEOUT=2.0,
        ):
            verdict = ClamAVScanner().scan(io.BytesIO(b"x"), name="a.bin")
        self.assertEqual(verdict.status, FileScan.Status.ERROR)
        self.assertTrue(verdict.detail)

    def test_silent_daemon_times_out_into_an_error_verdict(self):
        with FakeClamd(stall=True) as daemon, _tcp(daemon, timeout=0.5):
            verdict = ClamAVScanner().scan(io.BytesIO(b"x"), name="a.bin")
        self.assertEqual(verdict.status, FileScan.Status.ERROR)

    def test_the_whole_stream_reaches_the_daemon(self):
        payload = b"abcdefghij" * 300
        with FakeClamd(reply=b"stream: OK\n") as daemon, _tcp(daemon):
            ClamAVScanner().scan(io.BytesIO(payload), name="a.bin")
            self.assertEqual(daemon.received, payload)

    def test_health_reports_reachable_with_a_version(self):
        with FakeClamd() as daemon, _tcp(daemon):
            health = ClamAVScanner().health()
        self.assertTrue(health.reachable)
        self.assertIn("ClamAV", health.version)

    def test_health_reports_unreachable_when_nothing_listens(self):
        port = free_port()
        with override_settings(
            FILES_CLAMAV_SOCKET="",
            FILES_CLAMAV_HOST="127.0.0.1",
            FILES_CLAMAV_PORT=port,
            FILES_CLAMAV_TIMEOUT=2.0,
        ):
            health = ClamAVScanner().health()
        self.assertFalse(health.reachable)
        self.assertTrue(health.error)

    def _track_sockets(self):
        """Weak references to every socket clamdpy opens while the test runs.

        A ResourceWarning raised while a socket is finalized during garbage
        collection is swallowed by the interpreter, so it can never fail a
        test. A socket nothing refers to any more is closed on the spot, so a
        dead reference after the call proves none is left open - a worker
        whose daemon is down would otherwise lose one descriptor per scan.
        """
        opened = []

        class Tracked(socket.socket):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                opened.append(weakref.ref(self))

        tracked = types.SimpleNamespace(
            socket=Tracked,
            AF_INET=socket.AF_INET,
            AF_UNIX=socket.AF_UNIX,
            SOCK_STREAM=socket.SOCK_STREAM,
        )
        self.enterContext(patch.object(clamd_sockets, "socket", tracked))
        return opened

    def _assert_all_released(self, opened):
        self.assertTrue(opened, "the daemon was never contacted")
        self.assertEqual([ref for ref in opened if ref() is not None], [])

    def test_unreachable_daemon_does_not_leak_a_socket(self):
        port = free_port()
        opened = self._track_sockets()
        with override_settings(
            FILES_CLAMAV_SOCKET="",
            FILES_CLAMAV_HOST="127.0.0.1",
            FILES_CLAMAV_PORT=port,
            FILES_CLAMAV_TIMEOUT=2.0,
        ):
            ClamAVScanner().scan(io.BytesIO(b"x"), name="a.bin")
        self._assert_all_released(opened)

    def test_health_check_does_not_leak_a_socket_when_unreachable(self):
        port = free_port()
        opened = self._track_sockets()
        with override_settings(
            FILES_CLAMAV_SOCKET="",
            FILES_CLAMAV_HOST="127.0.0.1",
            FILES_CLAMAV_PORT=port,
            FILES_CLAMAV_TIMEOUT=2.0,
        ):
            ClamAVScanner().health()
        self._assert_all_released(opened)

    def test_scan_does_not_leak_a_socket_when_the_daemon_answers(self):
        opened = self._track_sockets()
        with FakeClamd(reply=b"stream: OK\n") as daemon, _tcp(daemon):
            ClamAVScanner().scan(io.BytesIO(b"harmless"), name="a.txt")
        self._assert_all_released(opened)

    def test_connection_failure_detail_is_a_sentence_not_a_tuple(self):
        port = free_port()
        with override_settings(
            FILES_CLAMAV_SOCKET="",
            FILES_CLAMAV_HOST="127.0.0.1",
            FILES_CLAMAV_PORT=port,
            FILES_CLAMAV_TIMEOUT=2.0,
        ):
            scan = ClamAVScanner().scan(io.BytesIO(b"x"), name="a.bin")
            health = ClamAVScanner().health()
        for detail in (scan.detail, health.error):
            self.assertTrue(detail.startswith("Error while connecting to"), detail)

    def test_unparseable_reply_is_an_error_verdict(self):
        with FakeClamd(reply=b"\n") as daemon, _tcp(daemon):
            verdict = ClamAVScanner().scan(io.BytesIO(b"x"), name="a.bin")
        self.assertEqual(verdict.status, FileScan.Status.ERROR)
        self.assertTrue(verdict.detail)


@unittest.skipUnless(hasattr(socket, "AF_UNIX"), "Unix sockets only")
class ClamAVUnixSocketTests(SimpleTestCase):
    """``FILES_CLAMAV_SOCKET`` takes a bare path or a ``unix://`` URL."""

    def _scan_through(self, configured):
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "clamd.ctl")
            with (
                FakeClamd(reply=b"stream: OK\n", unix_path=path) as daemon,
                override_settings(FILES_CLAMAV_SOCKET=configured.format(path=path)),
            ):
                verdict = ClamAVScanner().scan(io.BytesIO(b"payload"), name="a.txt")
                health = ClamAVScanner().health()
                return verdict, health, daemon.received

    def test_a_bare_path_reaches_the_daemon(self):
        verdict, health, received = self._scan_through("{path}")
        self.assertEqual(verdict.status, FileScan.Status.CLEAN, verdict.detail)
        self.assertTrue(health.reachable, health.error)
        self.assertEqual(received, b"payload")

    def test_a_unix_url_reaches_the_daemon(self):
        verdict, health, received = self._scan_through("unix://{path}")
        self.assertEqual(verdict.status, FileScan.Status.CLEAN, verdict.detail)
        self.assertTrue(health.reachable, health.error)
        self.assertEqual(received, b"payload")
