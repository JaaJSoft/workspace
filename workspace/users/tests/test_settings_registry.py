import uuid

from django.test import SimpleTestCase
from rest_framework import serializers

from workspace.users.settings_registry import (
    Preferences,
    UnknownModule,
    UserSettingsRegistry,
)


class _PanelOptions(Preferences):
    compact = serializers.BooleanField()
    folder = serializers.UUIDField()


class _GardenSettings(serializers.Serializer):
    rows = serializers.IntegerField(min_value=1, max_value=9)
    shed = serializers.UUIDField()
    panel = _PanelOptions()
    last_visit = serializers.CharField(read_only=True)


class UserSettingsRegistryTests(SimpleTestCase):
    def setUp(self):
        self.registry = UserSettingsRegistry()
        self.registry.register("garden", _GardenSettings)

    def test_a_module_registers_once(self):
        with self.assertRaises(ValueError):
            self.registry.register("garden", _GardenSettings)

    def test_declared_lists_every_key_read_only_ones_included(self):
        self.assertEqual(
            self.registry.declared(),
            {"garden": {"rows", "shed", "panel", "last_visit"}},
        )

    def test_only_a_declared_key_the_client_may_send_is_writable(self):
        self.assertTrue(self.registry.is_writable("garden", "rows"))
        self.assertFalse(self.registry.is_writable("garden", "last_visit"))
        self.assertFalse(self.registry.is_writable("garden", "weeds"))
        self.assertFalse(self.registry.is_writable("orchard", "rows"))

    def test_an_unknown_module_is_its_own_error(self):
        with self.assertRaises(UnknownModule):
            self.registry.clean("orchard", {"rows": 3})

    def test_a_value_comes_back_as_its_field_read_it(self):
        self.assertEqual(self.registry.clean("garden", {"rows": "3"}), {"rows": 3})

    def test_a_value_comes_back_as_json(self):
        shed = uuid.uuid4()
        cleaned = self.registry.clean("garden", {"shed": str(shed).upper()})
        self.assertEqual(cleaned, {"shed": str(shed)})

    def test_a_null_value_is_a_key_to_remove(self):
        self.assertEqual(
            self.registry.clean("garden", {"rows": None, "shed": None}),
            {"rows": None, "shed": None},
        )

    def test_a_refused_value_is_reported_under_its_key(self):
        with self.assertRaises(serializers.ValidationError) as raised:
            self.registry.clean("garden", {"rows": 12, "shed": "next door"})
        self.assertEqual(set(raised.exception.detail), {"rows", "shed"})

    def test_an_undeclared_or_read_only_key_is_refused(self):
        for key in ("weeds", "last_visit"):
            with self.subTest(key=key):
                with self.assertRaises(serializers.ValidationError) as raised:
                    self.registry.clean("garden", {"rows": 3, key: "x"})
                self.assertEqual(set(raised.exception.detail), {key})

    def test_preferences_keep_only_the_options_declared_and_set(self):
        folder = str(uuid.uuid4())
        cleaned = self.registry.clean(
            "garden",
            {"panel": {"compact": True, "folder": folder, "retired": [1, 2]}},
        )
        self.assertEqual(cleaned, {"panel": {"compact": True, "folder": folder}})
        cleaned = self.registry.clean(
            "garden", {"panel": {"compact": False, "folder": None}}
        )
        self.assertEqual(cleaned, {"panel": {"compact": False}})

    def test_preferences_refuse_a_value_an_option_does_not_take(self):
        for value in ({"compact": "maybe"}, ["compact"], "compact", 3):
            with self.subTest(value=value):
                with self.assertRaises(serializers.ValidationError):
                    self.registry.clean("garden", {"panel": value})
