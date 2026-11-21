"""Name resolution and TCP replaced for the guarded transport, so a test can
watch which address it dials without touching the network."""

import socket
import time
from unittest import mock

import httpcore2

OK = b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def http_response(status, body=b"", content_type="application/xml"):
    head = (
        f"HTTP/1.1 {status} X\r\nContent-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode() + body


class FakeNetwork:
    """``answers`` maps a hostname to the address lists successive lookups
    return, the last one repeating; any other name does not resolve.

    Every connection is recorded: ``dialed`` holds ``(host, port)``,
    ``server_names`` the TLS server name, ``sent`` the bytes written. It
    answers ``response``, or raises ``ConnectError`` for an address in
    ``unreachable``. With ``trickle`` set, it answers one byte per read, each
    that many seconds after the last, as a slow-loris server would.
    """

    def __init__(self, answers, response=OK, unreachable=(), trickle=None):
        self._answers = {host: list(lists) for host, lists in answers.items()}
        self.response = response
        self.unreachable = set(unreachable)
        self.trickle = trickle
        self.lookups = []
        self.dialed = []
        self.server_names = []
        self.sent = bytearray()

    def __enter__(self):
        self._patches = [
            mock.patch("socket.getaddrinfo", self._getaddrinfo),
            mock.patch.object(
                httpcore2.SyncBackend,
                "connect_tcp",
                autospec=True,
                side_effect=self._connect,
            ),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *exc):
        for patch in self._patches:
            patch.stop()

    def _getaddrinfo(self, host, port, *args, **kwargs):
        self.lookups.append(host)
        lists = self._answers.get(host)
        if not lists:
            raise socket.gaierror(socket.EAI_NONAME, "unknown")
        addresses = lists.pop(0) if len(lists) > 1 else lists[0]
        return [
            (
                socket.AF_INET6 if ":" in ip else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (ip, port or 0),
            )
            for ip in addresses
        ]

    def _connect(self, backend, host, port, *args, **kwargs):
        self.dialed.append((host, port))
        if host in self.unreachable:
            raise httpcore2.ConnectError(f"{host} is unreachable")
        return _Stream(self)


class _Stream(httpcore2.MockStream):
    def __init__(self, network):
        response = network.response
        if network.trickle is None:
            super().__init__([response])
        else:
            super().__init__([response[i : i + 1] for i in range(len(response))])
        self._network = network

    def read(self, max_bytes, timeout=None):
        if self._network.trickle is not None:
            time.sleep(self._network.trickle)
        return super().read(max_bytes, timeout)

    def write(self, buffer, timeout=None):
        self._network.sent += buffer

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        self._network.server_names.append(server_hostname)
        return self
