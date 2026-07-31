from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

User = get_user_model()

PUBLIC_IP = "203.0.113.7"


@override_settings(METRICS_TOKEN="", METRICS_ALLOWED_IPS=["127.0.0.0/8"])
class MetricsAccessTests(TestCase):
    def test_public_ip_is_rejected(self):
        resp = self.client.get("/metrics", REMOTE_ADDR=PUBLIC_IP)
        self.assertEqual(resp.status_code, 403)
        self.assertNotIn(b"python_info", resp.content)

    def test_allowlisted_ip_is_served(self):
        resp = self.client.get("/metrics", REMOTE_ADDR="127.0.0.1")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/plain", resp["Content-Type"])

    def test_ip_outside_allowlisted_network_is_rejected(self):
        with override_settings(METRICS_ALLOWED_IPS=["10.0.0.0/8"]):
            self.assertEqual(
                self.client.get("/metrics", REMOTE_ADDR="11.0.0.1").status_code, 403
            )
            self.assertEqual(
                self.client.get("/metrics", REMOTE_ADDR="10.4.2.1").status_code, 200
            )

    def test_ipv6_loopback_allowlist(self):
        with override_settings(METRICS_ALLOWED_IPS=["::1/128"]):
            self.assertEqual(
                self.client.get("/metrics", REMOTE_ADDR="::1").status_code, 200
            )
            self.assertEqual(
                self.client.get("/metrics", REMOTE_ADDR="2001:db8::1").status_code, 403
            )

    def test_empty_allowlist_rejects_every_ip(self):
        with override_settings(METRICS_ALLOWED_IPS=[]):
            self.assertEqual(
                self.client.get("/metrics", REMOTE_ADDR="127.0.0.1").status_code, 403
            )

    def test_malformed_remote_addr_is_rejected(self):
        self.assertEqual(
            self.client.get("/metrics", REMOTE_ADDR="not-an-ip").status_code, 403
        )

    def test_invalid_allowlist_entry_is_ignored(self):
        with override_settings(METRICS_ALLOWED_IPS=["nonsense", "127.0.0.0/8"]):
            self.assertEqual(
                self.client.get("/metrics", REMOTE_ADDR="127.0.0.1").status_code, 200
            )

    def test_forwarded_for_header_does_not_grant_access(self):
        resp = self.client.get(
            "/metrics", REMOTE_ADDR=PUBLIC_IP, HTTP_X_FORWARDED_FOR="127.0.0.1"
        )
        self.assertEqual(resp.status_code, 403)


@override_settings(METRICS_TOKEN="s3cret-token", METRICS_ALLOWED_IPS=[])
class MetricsTokenTests(TestCase):
    def test_valid_bearer_token_is_served(self):
        resp = self.client.get(
            "/metrics", REMOTE_ADDR=PUBLIC_IP, HTTP_AUTHORIZATION="Bearer s3cret-token"
        )
        self.assertEqual(resp.status_code, 200)

    def test_wrong_token_is_rejected(self):
        resp = self.client.get(
            "/metrics", REMOTE_ADDR=PUBLIC_IP, HTTP_AUTHORIZATION="Bearer nope"
        )
        self.assertEqual(resp.status_code, 403)

    def test_missing_authorization_header_is_rejected(self):
        self.assertEqual(
            self.client.get("/metrics", REMOTE_ADDR=PUBLIC_IP).status_code, 403
        )

    def test_wrong_scheme_is_rejected(self):
        resp = self.client.get(
            "/metrics", REMOTE_ADDR=PUBLIC_IP, HTTP_AUTHORIZATION="Basic s3cret-token"
        )
        self.assertEqual(resp.status_code, 403)

    def test_non_ascii_token_does_not_crash(self):
        resp = self.client.get(
            "/metrics", REMOTE_ADDR=PUBLIC_IP, HTTP_AUTHORIZATION="Bearer sécret"
        )
        self.assertEqual(resp.status_code, 403)

    def test_unset_token_never_matches(self):
        with override_settings(METRICS_TOKEN=""):
            resp = self.client.get(
                "/metrics", REMOTE_ADDR=PUBLIC_IP, HTTP_AUTHORIZATION="Bearer "
            )
            self.assertEqual(resp.status_code, 403)


@override_settings(METRICS_TOKEN="", METRICS_ALLOWED_IPS=[])
class MetricsSuperuserTests(TestCase):
    def test_superuser_session_is_served(self):
        User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        self.client.login(username="root", password="pw")
        self.assertEqual(
            self.client.get("/metrics", REMOTE_ADDR=PUBLIC_IP).status_code, 200
        )

    def test_regular_user_is_rejected(self):
        User.objects.create_user(username="bob", email="bob@example.com", password="pw")
        self.client.login(username="bob", password="pw")
        self.assertEqual(
            self.client.get("/metrics", REMOTE_ADDR=PUBLIC_IP).status_code, 403
        )

    def test_anonymous_user_is_rejected(self):
        self.assertEqual(
            self.client.get("/metrics", REMOTE_ADDR=PUBLIC_IP).status_code, 403
        )
