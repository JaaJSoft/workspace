from django.test import SimpleTestCase

from workspace.imports.providers.base import KIND_FILES, Provider
from workspace.imports.providers.registry import ProviderRegistry, provider_registry


class _Stub(Provider):
    slug = "stub"
    name = "Stub"
    kinds = frozenset({KIND_FILES})

    def __init__(self, available=True):
        self._available = available

    def is_available(self):
        return self._available

    def test_connection(self, connection):
        return {}


class ProviderRegistryTests(SimpleTestCase):
    def test_builtin_providers_are_registered_at_startup(self):
        self.assertLessEqual(
            {"webdav", "nextcloud"}, {p.slug for p in provider_registry.available()}
        )
        self.assertEqual(
            provider_registry.get("nextcloud").describe()["auth"], "credentials"
        )
        self.assertEqual(provider_registry.get("webdav").describe()["kinds"], ["files"])

    def test_duplicate_slug_is_rejected(self):
        reg = ProviderRegistry()
        reg.register(_Stub())
        with self.assertRaises(ValueError):
            reg.register(_Stub())

    def test_available_filters_on_is_available(self):
        reg = ProviderRegistry()
        reg.register(_Stub(available=False))
        self.assertEqual(reg.all()[0].slug, "stub")
        self.assertEqual(reg.available(), [])

    def test_file_source_is_not_implemented_by_default(self):
        with self.assertRaises(NotImplementedError):
            _Stub().file_source(None)
