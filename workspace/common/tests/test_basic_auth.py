import base64

from django.test import SimpleTestCase

from workspace.common.basic_auth import basic_credentials


def _header(value):
    return "Basic " + base64.b64encode(value.encode()).decode()


class BasicCredentialsTests(SimpleTestCase):
    def test_splits_user_and_password(self):
        self.assertEqual(
            basic_credentials(_header("alice:s3:cret")), ("alice", "s3:cret")
        )

    def test_scheme_is_case_insensitive(self):
        header = _header("alice:pw").replace("Basic", "basic")
        self.assertEqual(basic_credentials(header), ("alice", "pw"))

    def test_rejects_missing_or_foreign_headers(self):
        for header in (
            None,
            "",
            "Bearer abc",
            "Basic",
            "Basic !!!",
            _header("nocolon"),
        ):
            with self.subTest(header=header):
                self.assertIsNone(basic_credentials(header))
