import base64

from django.test import TestCase, override_settings

CREDENTIALS = {"METRICS_USER": "prom", "METRICS_PASSWORD": "s3cret"}


def basic(user, password):
    encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {encoded}"


@override_settings(**CREDENTIALS)
class MetricsBasicAuthTests(TestCase):
    def test_valid_credentials_are_served(self):
        resp = self.client.get("/metrics", HTTP_AUTHORIZATION=basic("prom", "s3cret"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/plain", resp["Content-Type"])

    def test_anonymous_request_is_challenged(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 401)
        self.assertIn("Basic", resp["WWW-Authenticate"])
        self.assertNotIn(b"python_info", resp.content)

    def test_wrong_password_is_rejected(self):
        resp = self.client.get("/metrics", HTTP_AUTHORIZATION=basic("prom", "nope"))
        self.assertEqual(resp.status_code, 401)

    def test_wrong_user_is_rejected(self):
        resp = self.client.get(
            "/metrics", HTTP_AUTHORIZATION=basic("mallory", "s3cret")
        )
        self.assertEqual(resp.status_code, 401)

    def test_wrong_scheme_is_rejected(self):
        resp = self.client.get("/metrics", HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(resp.status_code, 401)

    def test_malformed_base64_is_rejected(self):
        resp = self.client.get("/metrics", HTTP_AUTHORIZATION="Basic not-base64!!")
        self.assertEqual(resp.status_code, 401)

    def test_payload_without_colon_is_rejected(self):
        encoded = base64.b64encode(b"promonly").decode()
        resp = self.client.get("/metrics", HTTP_AUTHORIZATION=f"Basic {encoded}")
        self.assertEqual(resp.status_code, 401)

    def test_non_utf8_payload_is_rejected(self):
        encoded = base64.b64encode(b"\xff\xfe:\xff").decode()
        resp = self.client.get("/metrics", HTTP_AUTHORIZATION=f"Basic {encoded}")
        self.assertEqual(resp.status_code, 401)

    def test_password_containing_a_colon_round_trips(self):
        with override_settings(METRICS_PASSWORD="a:b:c"):
            resp = self.client.get(
                "/metrics", HTTP_AUTHORIZATION=basic("prom", "a:b:c")
            )
            self.assertEqual(resp.status_code, 200)

    def test_non_ascii_credentials_round_trip(self):
        with override_settings(METRICS_PASSWORD="mot-de-passé"):
            resp = self.client.get(
                "/metrics", HTTP_AUTHORIZATION=basic("prom", "mot-de-passé")
            )
            self.assertEqual(resp.status_code, 200)


class MetricsUnconfiguredTests(TestCase):
    """Missing credentials must close the endpoint, never open it."""

    @override_settings(METRICS_USER="", METRICS_PASSWORD="")
    def test_unset_credentials_reject_everyone(self):
        self.assertEqual(self.client.get("/metrics").status_code, 401)

    @override_settings(METRICS_USER="", METRICS_PASSWORD="")
    def test_unset_credentials_reject_empty_basic_header(self):
        resp = self.client.get("/metrics", HTTP_AUTHORIZATION=basic("", ""))
        self.assertEqual(resp.status_code, 401)

    @override_settings(METRICS_USER="prom", METRICS_PASSWORD="")
    def test_half_configured_credentials_reject_everyone(self):
        resp = self.client.get("/metrics", HTTP_AUTHORIZATION=basic("prom", ""))
        self.assertEqual(resp.status_code, 401)
