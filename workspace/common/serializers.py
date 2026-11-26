"""What every serializer of the API is built from."""

from datetime import UTC

from django.db import models
from django.utils import timezone
from rest_framework import serializers


class InstantField(serializers.DateTimeField):
    """An instant, written in UTC with a ``Z`` whoever asks.

    DRF's own field writes it in the active zone, which is the caller's in a
    session request and UTC for a token: the same row would read differently
    from the page and from a script. Reading is unchanged, so a value sent
    without an offset is still taken in the caller's zone.
    """

    def to_representation(self, value):
        if isinstance(value, str):
            return value
        if timezone.is_naive(value):
            value = timezone.make_aware(value, UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# No docstring: the schema would print it as the description of every resource.
class ApiModelSerializer(serializers.ModelSerializer):
    serializer_field_mapping = {
        **serializers.ModelSerializer.serializer_field_mapping,
        models.DateTimeField: InstantField,
    }
