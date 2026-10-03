from django.template import engines
from django.template.loaders.cached import Loader as CachedLoader
from django.test import SimpleTestCase


class TemplateLoaderCachingTests(SimpleTestCase):
    def test_compiled_templates_are_cached(self):
        # The suite runs with the DEBUG the settings were imported under (true by default),
        # so this also covers development.
        loaders = engines["django"].engine.template_loaders
        self.assertEqual(len(loaders), 1)
        self.assertIsInstance(loaders[0], CachedLoader)
        self.assertEqual(
            [type(loader).__module__ for loader in loaders[0].loaders],
            [
                "django.template.loaders.filesystem",
                "django.template.loaders.app_directories",
            ],
        )
