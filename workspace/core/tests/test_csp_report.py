"""The endpoint browsers post Content-Security-Policy violations to.

A report is written by the page that was refused, so every field in it is
attacker-reachable: it is logged sanitised, capped before it is read, and
rate-limited per address.
"""

import json
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from drf_spectacular.generators import SchemaGenerator
from rest_framework.throttling import SimpleRateThrottle

from workspace.core.views.csp_report import MAX_REPORT_BYTES, CspReportIpThrottle

User = get_user_model()

URL = "/api/v1/csp-report"
LOGGER = "workspace.core.csp_report"


def csp_report(**fields):
    body = {
        "document-uri": "http://testserver/vault?token=secret#frag",
        "effective-directive": "img-src",
        "violated-directive": "img-src",
        "blocked-uri": "https://blocked.example/pixel.png?leak=1",
        "script-sample": "const password = 'hunter2'",
        "disposition": "enforce",
    }
    body.update(fields)
    return json.dumps({"csp-report": body})


class CspReportViewTests(TestCase):
    def tearDown(self):
        cache.clear()

    def post(self, body, content_type="application/csp-report", client=None):
        return (client or self.client).post(URL, data=body, content_type=content_type)

    def logged_line(self, body):
        with self.assertLogs(LOGGER, "WARNING") as logs:
            response = self.post(body)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(logs.records), 1)
        return logs.records[0].getMessage()

    def test_a_report_is_logged_and_acknowledged(self):
        line = self.logged_line(csp_report())
        self.assertIn("img-src", line)
        self.assertIn("https://blocked.example/pixel.png", line)
        self.assertIn("http://testserver/vault", line)

    def test_the_violated_directive_stands_in_for_a_missing_effective_one(self):
        line = self.logged_line(
            csp_report(
                **{"effective-directive": None, "violated-directive": "script-src"}
            )
        )
        self.assertIn("script-src", line)

    def test_query_strings_and_fragments_never_reach_the_log(self):
        """A URL can carry a token, and the secret-redaction filter matches
        field names, not the inside of a URL."""
        line = self.logged_line(csp_report())
        self.assertNotIn("leak=1", line)
        self.assertNotIn("token=secret", line)
        self.assertNotIn("frag", line)

    def test_a_keyword_in_place_of_a_url_is_kept(self):
        line = self.logged_line(csp_report(**{"blocked-uri": "inline"}))
        self.assertIn("refused inline on", line)

    def test_the_script_sample_is_never_logged(self):
        """It is the start of whatever the page refused, which on a vault page
        can be anything the page was holding."""
        line = self.logged_line(csp_report())
        self.assertNotIn("hunter2", line)

    def test_line_breaks_cannot_forge_a_log_line(self):
        line = self.logged_line(
            csp_report(**{"effective-directive": "img-src\r\nCSP violation: forged"})
        )
        self.assertNotIn("\n", line)
        self.assertNotIn("\r", line)

    def test_a_signed_in_browser_is_not_asked_for_a_csrf_token(self):
        """A report-uri request carries the page's cookies and no CSRF token.
        Session authentication would enforce CSRF on it and every report from
        a signed-in page would bounce with a 403."""
        client = Client(enforce_csrf_checks=True)
        client.force_login(User.objects.create_user(username="reporter", password="pw"))
        response = self.post(csp_report(), client=client)
        self.assertEqual(response.status_code, 204)

    def test_an_anonymous_browser_can_report(self):
        self.assertEqual(self.post(csp_report()).status_code, 204)

    def test_the_plain_json_content_type_is_accepted(self):
        response = self.post(csp_report(), content_type="application/json")
        self.assertEqual(response.status_code, 204)

    def test_an_oversized_report_is_refused_before_it_is_read(self):
        body = csp_report(**{"script-sample": "x" * MAX_REPORT_BYTES})
        with self.assertNoLogs(LOGGER):
            response = self.post(body)
        self.assertEqual(response.status_code, 413)

    def test_another_content_type_is_refused(self):
        with self.assertNoLogs(LOGGER):
            response = self.post(csp_report(), content_type="text/plain")
        self.assertEqual(response.status_code, 415)

    def test_a_malformed_report_is_refused(self):
        for body in (
            "not json",
            json.dumps({"other": {}}),
            json.dumps({"csp-report": []}),
        ):
            with self.subTest(body=body), self.assertNoLogs(LOGGER):
                self.assertEqual(self.post(body).status_code, 400)

    def test_one_address_cannot_flood_the_log(self):
        with patch.dict(
            SimpleRateThrottle.THROTTLE_RATES, {"core.csp_report.ip": "2/min"}
        ):
            statuses = [self.post(csp_report()).status_code for _ in range(3)]
        self.assertEqual(statuses, [204, 204, 429])

    def test_the_throttle_scope_has_a_configured_rate(self):
        """A throttle whose scope has no rate is inert, and DRF says nothing."""
        self.assertIn(
            CspReportIpThrottle.scope, settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        )

    def test_the_endpoint_stays_out_of_the_public_schema(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        self.assertFalse(
            [path for path in schema["paths"] if path.endswith("csp-report")]
        )
