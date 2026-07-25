from django.apps import apps
from django.test import TestCase


class AppConfigTests(TestCase):
    def test_app_is_installed(self):
        config = apps.get_app_config("projects")
        self.assertEqual(config.name, "workspace.projects")
