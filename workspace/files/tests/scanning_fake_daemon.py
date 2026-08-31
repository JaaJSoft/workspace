"""An in-process stand-in for the clamd daemon.

Lets the ClamAV backend be tested end to end - real sockets, real INSTREAM
framing - without ClamAV installed anywhere. The fake decides the verdict, so
no test needs a malicious payload; there is deliberately no EICAR string in
this repository.
"""

from __future__ import annotations

import socketserver
import struct
import threading


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        command = self.rfile.readline().strip()
        if self.server.stall:
            # Accept, read, answer nothing: exercises the client's socket
            # timeout rather than a connection failure.
            threading.Event().wait(5)
            return
        if command == b"nPING":
            self.wfile.write(b"PONG\n")
            return
        if command == b"nVERSION":
            self.wfile.write(b"ClamAV 1.4.1/27000/Fri Aug 28 09:00:00 2026\n")
            return
        if command == b"nINSTREAM":
            self.server.received = self._drain_stream()
            self.wfile.write(self.server.reply)
            return
        self.wfile.write(b"UNKNOWN COMMAND\n")

    def _drain_stream(self):
        body = bytearray()
        while True:
            header = self.rfile.read(4)
            if len(header) < 4:
                break
            (length,) = struct.unpack(b"!L", header)
            if length == 0:
                break
            body += self.rfile.read(length)
        return bytes(body)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeClamd:
    """Context manager running the fake daemon on an ephemeral local port.

    ``reply`` is the exact bytes written in answer to INSTREAM, newline
    included. ``received`` holds the bytes the last INSTREAM delivered, so a
    test can assert the cap was honoured.
    """

    def __init__(self, reply=b"stream: OK\n", stall=False):
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.reply = reply
        self._server.stall = stall
        self._server.received = b""
        self.host, self.port = self._server.server_address[:2]

    @property
    def received(self):
        return self._server.received

    def __enter__(self):
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        return False


def free_port():
    """A port nothing listens on, for the connection-refused case."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
