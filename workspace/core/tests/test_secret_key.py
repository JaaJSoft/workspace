"""SECRET_KEY signs sessions and derives the key stored credentials are
encrypted with. The development key and the placeholder of the deployment
examples are public, so an instance outside DEBUG must not start on them."""

import os
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from workspace.settings.env import _DEVELOPMENT_SECRET_KEY, env_secret_key


def _environment(**variables):
    cleared = {k: v for k, v in os.environ.items() if k != "SECRET_KEY"}
    return patch.dict(os.environ, {**cleared, **variables}, clear=True)


class SecretKeyTests(SimpleTestCase):
    def test_a_missing_key_refuses_to_start_outside_debug(self):
        with _environment(), self.assertRaises(ImproperlyConfigured):
            env_secret_key(debug=False)

    def test_an_empty_key_refuses_to_start_outside_debug(self):
        with _environment(SECRET_KEY=""), self.assertRaises(ImproperlyConfigured):
            env_secret_key(debug=False)

    def test_the_published_keys_refuse_to_start_outside_debug(self):
        for published in (_DEVELOPMENT_SECRET_KEY, "change-me-to-a-real-secret-key"):
            with (
                self.subTest(key=published),
                _environment(SECRET_KEY=published),
                self.assertRaises(ImproperlyConfigured),
            ):
                env_secret_key(debug=False)

    def test_a_key_of_ones_own_is_used(self):
        with _environment(SECRET_KEY="a-key-nobody-published"):
            self.assertEqual(env_secret_key(debug=False), "a-key-nobody-published")

    def test_development_runs_without_a_key(self):
        with _environment():
            self.assertEqual(env_secret_key(debug=True), _DEVELOPMENT_SECRET_KEY)
