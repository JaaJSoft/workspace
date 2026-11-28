"""Nextcloud Login Flow v2, against a scripted remote."""

from contextlib import contextmanager
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx2
from django.test import SimpleTestCase

from workspace.common.url_guard import UnsafeUrl
from workspace.imports.providers.base import (
    ConnectionFailed,
    LoginFlowGrant,
    LoginFlowStart,
    ProviderError,
)
from workspace.imports.providers.nextcloud import NextcloudProvider

BASE = "https://cloud.example.org"
POLL = f"{BASE}/login/v2/poll"
LOGIN = f"{BASE}/login/v2/flow/abc"


def _start_answer(endpoint=POLL, login=LOGIN, token="tok"):
    return {"poll": {"token": token, "endpoint": endpoint}, "login": login}


@contextmanager
def _remote(handler):
    """Every request of the flow goes to *handler* instead of the network."""
    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    with patch(
        "workspace.imports.providers.nextcloud.guarded_transport",
        lambda policy: httpx2.MockTransport(record),
    ):
        yield requests


class StartLoginFlowTests(SimpleTestCase):
    def _start(self, answer, base_url=BASE):
        with _remote(lambda request: answer) as requests:
            result = NextcloudProvider().start_login_flow(base_url)
        return result, requests

    def test_opens_the_flow_on_the_instance_as_workspace(self):
        start, requests = self._start(httpx2.Response(200, json=_start_answer()))

        self.assertEqual(
            start, LoginFlowStart(login_url=LOGIN, poll_endpoint=POLL, poll_token="tok")
        )
        [request] = requests
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), f"{BASE}/index.php/login/v2")
        # Nextcloud names the app password after it, in the user's devices.
        self.assertEqual(request.headers["User-Agent"], "Workspace")
        self.assertNotIn("Authorization", request.headers)

    def test_an_instance_under_a_sub_path(self):
        base = "https://example.org/nextcloud"
        answer = _start_answer(
            endpoint=f"{base}/login/v2/poll", login=f"{base}/login/v2/flow/abc"
        )
        _, requests = self._start(httpx2.Response(200, json=answer), base_url=base)
        self.assertEqual(str(requests[0].url), f"{base}/index.php/login/v2")

    def test_a_poll_endpoint_on_another_host_is_refused(self):
        answer = _start_answer(endpoint="https://evil.example.net/login/v2/poll")
        with self.assertRaisesMessage(ProviderError, "another server"):
            self._start(httpx2.Response(200, json=answer))

    def test_a_sign_in_page_on_another_host_is_refused(self):
        answer = _start_answer(login="https://evil.example.net/login/v2/flow/abc")
        with self.assertRaisesMessage(ProviderError, "another server"):
            self._start(httpx2.Response(200, json=answer))

    def test_a_poll_endpoint_over_plain_http_is_refused(self):
        answer = _start_answer(endpoint="http://cloud.example.org/login/v2/poll")
        with self.assertRaisesMessage(ProviderError, "another server"):
            self._start(httpx2.Response(200, json=answer))

    def test_a_poll_endpoint_on_another_port_is_refused(self):
        answer = _start_answer(endpoint="https://cloud.example.org:8443/login/v2/poll")
        with self.assertRaisesMessage(ProviderError, "another server"):
            self._start(httpx2.Response(200, json=answer))

    def test_a_javascript_sign_in_page_is_refused(self):
        answer = _start_answer(login="javascript:alert(1)")
        with self.assertRaisesMessage(ProviderError, "another server"):
            self._start(httpx2.Response(200, json=answer))

    def test_addresses_a_browser_reads_as_another_host_are_refused(self):
        # urlparse reads the host after the "@" in each of these; a browser
        # takes "\" for "/" and goes to evil.example.net.
        for address in (
            r"https://evil.example.net\@cloud.example.org/login/v2/flow/abc",
            "https://evil.example.net@cloud.example.org/login/v2/flow/abc",
            "https://cloud.example.org\t.evil.example.net/login/v2/flow/abc",
        ):
            for field in ("login", "endpoint"):
                with self.subTest(address=address, field=field):
                    answer = _start_answer(**{field: address})
                    with self.assertRaisesMessage(ProviderError, "another server"):
                        self._start(httpx2.Response(200, json=answer))

    def test_the_default_port_written_out_is_the_same_server(self):
        answer = _start_answer(endpoint="https://cloud.example.org:443/login/v2/poll")
        start, _ = self._start(httpx2.Response(200, json=answer))
        self.assertEqual(
            start.poll_endpoint, "https://cloud.example.org:443/login/v2/poll"
        )

    def test_an_instance_without_the_flow_is_reported(self):
        with self.assertRaisesMessage(ProviderError, "Use an app password instead"):
            self._start(httpx2.Response(404))

    def test_an_answer_that_is_not_json_is_reported(self):
        with self.assertRaisesMessage(ProviderError, "Use an app password instead"):
            self._start(httpx2.Response(200, content=b"<html>login</html>"))

    def test_an_answer_missing_the_token_is_reported(self):
        answer = {"poll": {"endpoint": POLL}, "login": LOGIN}
        with self.assertRaisesMessage(ProviderError, "Use an app password instead"):
            self._start(httpx2.Response(200, json=answer))

    def test_an_oversized_answer_is_cut_off(self):
        with self.assertRaisesMessage(ProviderError, "too much data"):
            self._start(httpx2.Response(200, content=b"{" + b" " * 200_000 + b"}"))

    def test_an_unreachable_instance_is_a_connection_failure(self):
        def handler(request):
            raise httpx2.ConnectError("down")

        with _remote(handler), self.assertRaises(ConnectionFailed):
            NextcloudProvider().start_login_flow(BASE)


class PollLoginFlowTests(SimpleTestCase):
    def _poll(self, answer, endpoint=POLL):
        with _remote(lambda request: answer) as requests:
            result = NextcloudProvider().poll_login_flow(BASE, endpoint, "tok")
        return result, requests

    def test_pending_while_the_user_has_not_approved(self):
        result, requests = self._poll(httpx2.Response(404))

        self.assertIsNone(result)
        [request] = requests
        self.assertEqual(request.method, "POST")
        self.assertEqual(str(request.url), POLL)
        self.assertEqual(parse_qs(request.content.decode()), {"token": ["tok"]})

    def test_the_approval_hands_back_the_login_name_and_app_password(self):
        answer = {"server": BASE, "loginName": "alice", "appPassword": "app-pw"}
        result, _ = self._poll(httpx2.Response(200, json=answer))
        self.assertEqual(result, LoginFlowGrant(username="alice", secret="app-pw"))

    def test_an_approval_without_a_password_is_reported(self):
        with self.assertRaises(ProviderError):
            self._poll(httpx2.Response(200, json={"loginName": "alice"}))

    def test_a_server_error_is_a_connection_failure(self):
        with self.assertRaises(ConnectionFailed):
            self._poll(httpx2.Response(503))

    def test_a_poll_endpoint_on_another_host_is_never_asked(self):
        with _remote(lambda request: httpx2.Response(404)) as requests:
            with self.assertRaises(ProviderError):
                NextcloudProvider().poll_login_flow(
                    BASE, "https://evil.example.net/poll", "tok"
                )
        self.assertEqual(requests, [])

    def test_a_poll_endpoint_hiding_another_host_is_never_asked(self):
        with _remote(lambda request: httpx2.Response(404)) as requests:
            with self.assertRaises(ProviderError):
                NextcloudProvider().poll_login_flow(
                    BASE, r"https://evil.example.net\@cloud.example.org/poll", "tok"
                )
        self.assertEqual(requests, [])

    def test_the_poll_goes_through_the_url_guard(self):
        with self.assertRaises(UnsafeUrl):
            NextcloudProvider().poll_login_flow(
                "http://127.0.0.1", "http://127.0.0.1/login/v2/poll", "tok"
            )


class LoginFlowBaseUrlTests(SimpleTestCase):
    def test_the_instance_root_whatever_page_was_copied(self):
        provider = NextcloudProvider()
        for typed, expected in [
            ("https://cloud.example.org/", BASE),
            ("https://cloud.example.org/index.php/apps/files/", BASE),
            ("https://cloud.example.org/remote.php/dav/files/alice", BASE),
            ("https://example.org/nc/index.php/login", "https://example.org/nc"),
        ]:
            with self.subTest(typed=typed):
                self.assertEqual(provider.login_flow_base_url(typed), expected)
