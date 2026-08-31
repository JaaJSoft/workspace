import io
from unittest.mock import patch

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

    def _spy_on_client(self):
        """Capture every clamd client the scanner builds, real construction kept.

        A ResourceWarning raised while a socket's finalizer runs during
        garbage collection is swallowed by the interpreter ("Exception
        ignored while finalizing...") rather than propagated - it can never
        fail a test, even when the leak is real. Asserting the socket's
        ``fileno()`` is -1 (closed) after the call is the only reliable way
        to pin this down.
        """
        clients = []
        real_client = ClamAVScanner._client

        def spy(scanner, timeout=None):
            client = real_client(scanner, timeout=timeout)
            clients.append(client)
            return client

        self.enterContext(patch.object(ClamAVScanner, "_client", spy))
        return clients

    def test_unreachable_daemon_does_not_leak_a_socket(self):
        # clamav_client's _init_socket() assigns the socket before connect();
        # a connect failure can leave it open until GC finalizes it, one fd
        # per scan attempted while a Celery worker's daemon is down.
        port = free_port()
        clients = self._spy_on_client()
        with override_settings(
            FILES_CLAMAV_SOCKET="",
            FILES_CLAMAV_HOST="127.0.0.1",
            FILES_CLAMAV_PORT=port,
            FILES_CLAMAV_TIMEOUT=2.0,
        ):
            ClamAVScanner().scan(io.BytesIO(b"x"), name="a.bin")
        self.assertEqual(clients[0].clamd_socket.fileno(), -1)

    def test_health_check_does_not_leak_a_socket_when_unreachable(self):
        port = free_port()
        clients = self._spy_on_client()
        with override_settings(
            FILES_CLAMAV_SOCKET="",
            FILES_CLAMAV_HOST="127.0.0.1",
            FILES_CLAMAV_PORT=port,
            FILES_CLAMAV_TIMEOUT=2.0,
        ):
            ClamAVScanner().health()
        self.assertEqual(clients[0].clamd_socket.fileno(), -1)
