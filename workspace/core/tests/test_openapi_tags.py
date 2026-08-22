"""The OpenAPI tag registry and the tags used by views must stay in sync.

Swagger UI only shows a description and a controlled ordering for tags
declared in SPECTACULAR_SETTINGS["TAGS"]; a tag used by a view but missing
from the registry silently renders untitled at the bottom of the page, and
a declared tag no longer used by any view is dead weight.
"""

from django.conf import settings
from django.test import TestCase
from drf_spectacular.generators import SchemaGenerator


class OpenApiTagRegistryTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        schema = SchemaGenerator().get_schema(request=None, public=True)
        cls.used_tags = {
            tag
            for operations in schema["paths"].values()
            for operation in operations.values()
            if isinstance(operation, dict)
            for tag in operation.get("tags", [])
        }
        cls.declared_tags = {
            tag["name"] for tag in settings.SPECTACULAR_SETTINGS["TAGS"]
        }

    def test_every_used_tag_is_declared(self):
        undeclared = self.used_tags - self.declared_tags
        self.assertFalse(
            undeclared,
            f"Tags used by views but missing from SPECTACULAR_SETTINGS['TAGS'] "
            f"(add them with a description): {sorted(undeclared)}",
        )

    def test_every_declared_tag_is_used(self):
        unused = self.declared_tags - self.used_tags
        self.assertFalse(
            unused,
            f"Tags declared in SPECTACULAR_SETTINGS['TAGS'] but not used by any "
            f"view (remove them or fix the views): {sorted(unused)}",
        )
