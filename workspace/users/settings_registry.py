"""The user settings each module declares, and the one place a value is checked.

A module describes its settings with a serializer, one field per key, and
registers it from its ``AppConfig.ready()``::

    class PhotosSettings(serializers.Serializer):
        tile_size = serializers.IntegerField(min_value=1, max_value=5)
        import_folder = serializers.UUIDField(read_only=True)

    user_settings_registry.register("photos", PhotosSettings)

The settings API writes a key only when a registered field declares it, and
stores what the field made of the value. A ``read_only`` field is a setting
the server writes itself (``set_setting``): declared so the module's keys are
all in one place, refused when a client sends it.

``set_setting`` checks nothing. It is the storage primitive, for values the
server computed.
"""

import json
import threading
from collections.abc import Mapping

from rest_framework import serializers
from rest_framework.utils.encoders import JSONEncoder


class UnknownModule(LookupError):
    """No module registered settings under that name."""


def not_a_choice(value):
    """The error a ChoiceField raises, for a choice a field cannot enumerate."""
    message = serializers.ChoiceField.default_error_messages["invalid_choice"]
    return serializers.ValidationError(message.format(input=value))


class Preferences(serializers.Serializer):
    """Several options stored as one object under a single key.

    The page sends the whole object on every change, so a write replaces it. A
    null option is left out, like a null setting, and so is one the schema
    does not declare: an object stored before an option was retired still
    carries it, and refusing it would block every later save of that page.
    """

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            data = {key: value for key, value in data.items() if value is not None}
        return super().to_internal_value(data)


class UserSettingsRegistry:
    def __init__(self):
        self._schemas: dict[str, type[serializers.Serializer]] = {}
        self._lock = threading.Lock()

    def register(self, module: str, schema: type[serializers.Serializer]):
        with self._lock:
            if module in self._schemas:
                raise ValueError(f"Settings of '{module}' are already registered")
            self._schemas[module] = schema

    def fields(self, module: str) -> dict[str, serializers.Field]:
        """Every setting *module* declares, read-only ones included."""
        schema = self._schemas.get(module)
        return dict(schema().fields) if schema else {}

    def declared(self) -> dict[str, set[str]]:
        """The keys of every registered module."""
        return {module: set(self.fields(module)) for module in self._schemas}

    def is_writable(self, module: str, key: str) -> bool:
        field = self.fields(module).get(key)
        return field is not None and not field.read_only

    def clean(self, module: str, values: Mapping) -> dict:
        """What to store for *values*, a ``{key: value}`` a client sent.

        A null value comes back as None: the key is to be removed. Raises
        UnknownModule, or a ValidationError keyed by setting.
        """
        schema = self._schemas.get(module)
        if schema is None:
            raise UnknownModule(module)
        fields = schema().fields
        unknown = [key for key in values if key not in fields or fields[key].read_only]
        if unknown:
            raise serializers.ValidationError(
                dict.fromkeys(unknown, ["Unknown setting."])
            )
        present = {key: value for key, value in values.items() if value is not None}
        serializer = schema(data=present, partial=True)
        serializer.is_valid(raise_exception=True)
        # A field hands back Python values (a UUID, a tuple); the column holds JSON.
        stored = json.loads(json.dumps(serializer.validated_data, cls=JSONEncoder))
        return {key: stored.get(key) for key in values}


user_settings_registry = UserSettingsRegistry()
