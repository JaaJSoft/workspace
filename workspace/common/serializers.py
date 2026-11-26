"""What every serializer of the API is built from."""

from datetime import UTC

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.extensions import OpenApiSerializerFieldExtension
from drf_spectacular.plumbing import build_basic_type
from drf_spectacular.types import OpenApiTypes
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


class SummaryField(serializers.PrimaryKeyRelatedField):
    """A relation: a small object when read, its identifier when written.

    *summary* is the serializer of the small object - the identifier plus
    what displaying the row takes - so a client lists tasks without a request
    per status, and still writes ``"status": "<uuid>"`` under the same name.

    A writable one says which rows the caller may name: *narrow* takes the
    queryset and the serializer's context and answers the reachable part of
    it (the statuses of the project at hand, the users one may assign). An
    identifier outside it is refused with the message an unknown one gets.
    It is asked for rather than left to the serializer because forgetting it
    lets a caller attach a row of someone else's project.
    """

    def __init__(self, summary, *, narrow=None, **kwargs):
        assert narrow is not None or kwargs.get("read_only"), (
            "A writable SummaryField says which rows the caller may name: pass narrow=."
        )
        self.summary = summary
        self.narrow = narrow
        super().__init__(**kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        if queryset is None or self.narrow is None:
            return queryset
        return self.narrow(queryset, self.context)

    def use_pk_only_optimization(self):
        return False

    def to_representation(self, value):
        return self.summary(value, context=self.context).data

    def get_choices(self, cutoff=None):
        # The base keys the choices by to_representation(), a dict here.
        queryset = self.get_queryset()
        if queryset is None:
            return {}
        if cutoff is not None:
            queryset = queryset[:cutoff]
        return {str(item.pk): self.display_value(item) for item in queryset}


class SummaryFieldSchema(OpenApiSerializerFieldExtension):
    target_class = SummaryField

    def map_serializer_field(self, auto_schema, direction):
        if direction == "response":
            return auto_schema.resolve_serializer(self.target.summary, direction).ref
        key = self.target.queryset.model._meta.pk
        is_uuid = isinstance(key, models.UUIDField)
        return build_basic_type(OpenApiTypes.UUID if is_uuid else OpenApiTypes.INT)


# ``{"uuids": [...]}``: the rows a request is about, each named once. Capped
# at a thousand: a longer list is a job of its own, and SQLite binds one
# parameter per row a lot write touches. No docstring: the schema would print
# it as the description of every request built on it.
class UuidListSerializer(serializers.Serializer):
    uuids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False, max_length=1000
    )

    def validate_uuids(self, uuids):
        if len(set(uuids)) != len(uuids):
            raise serializers.ValidationError(_("The same uuid is listed twice."))
        return uuids


class ActionSerializer(serializers.Serializer):
    """One action a client may offer on a row, as an action registry lists it."""

    id = serializers.CharField()
    label = serializers.CharField()
    icon = serializers.CharField()
    category = serializers.CharField()
    css_class = serializers.CharField(allow_blank=True)
    bulk = serializers.BooleanField()
