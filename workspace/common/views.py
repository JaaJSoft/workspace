"""The views of the patterns every module repeats.

A module subclasses one and declares what is its own: the rows the caller can
reach (``get_queryset``), and what the request does to them. The shape of the
request, the refusal of a row the caller cannot see, the transaction and the
answer are written here once.
"""

from contextlib import contextmanager

from django.db import IntegrityError, transaction
from drf_spectacular.utils import (
    PolymorphicProxySerializer,
    extend_schema,
    extend_schema_view,
)
from rest_framework import serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

from .serializers import ActionSerializer, UuidListSerializer


@contextmanager
def taken_value_refused(field, message):
    """Refuse the write under *field* when a unique constraint trips.

    A serializer checks uniqueness first: this catches the writer that took
    the value in between, so the answer stays a validation error on the
    field and never a 500. The savepoint keeps the request's transaction
    usable after the constraint fired.
    """
    try:
        with transaction.atomic():
            yield
    except IntegrityError:
        raise ValidationError({field: [message]}) from None


def unknown_rows(uuids):
    """The refusal of a request naming rows that are not there, one message
    per row. A row the caller cannot see is refused in the same words."""
    unknown = serializers.PrimaryKeyRelatedField.default_error_messages[
        "does_not_exist"
    ]
    return ValidationError({"uuids": [unknown.format(pk_value=uuid) for uuid in uuids]})


def rows_in_order(queryset, uuids):
    """The rows of *queryset* named by *uuids*, in the order asked.

    One uuid outside *queryset* refuses the whole request, naming it: a lot
    is never applied to the part of it that was found.

    The rows are read, not locked - a module's services take their locks in
    their own order, and locking here first would invert it. A view whose
    service skips a row deleted since calls ``refuse_if_gone`` once the
    service holds its locks.
    """
    found = {row.pk: row for row in queryset.filter(pk__in=uuids)}
    missing = [uuid for uuid in uuids if uuid not in found]
    if missing:
        raise unknown_rows(missing)
    return [found[uuid] for uuid in uuids]


def refuse_if_gone(queryset, rows):
    """Refuse the request when one of *rows* left *queryset* since it was read.

    Called once the service has run, inside the request's transaction: the
    service holds its locks by then, so what is left cannot change anymore,
    and a service skips a row it does not find instead of failing. Raising
    undoes what it did to the others.
    """
    left = {row.pk for row in queryset.filter(pk__in=[row.pk for row in rows])}
    gone = [row.pk for row in rows if row.pk not in left]
    if gone:
        raise unknown_rows(gone)


class BatchView(GenericAPIView):
    """``POST /<resources>/batch``: one action on several rows, all or nothing.

    The body is ``{"action", "uuids", "data"}``. ``batch_actions`` maps each
    action to the serializer of its ``data``, or None when it takes none, and
    the view implements ``perform_<action>(rows, data)`` for each. Returning
    rows answers 200 with them, serialized by ``serializer_class``; returning
    None answers 204, as a delete does.

    Everything runs in one transaction: an exception raised from a
    ``perform_`` method - a ``ValidationError`` naming the rows that refuse
    the change - leaves no row changed.
    """

    # The component the schema names the request after: "Task" gives TaskBatch.
    batch_name = None
    batch_actions = {}
    # The rows answered are the ones the request named, never a page of them.
    pagination_class = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.batch_actions:
            return
        cls.batch_requests = {
            action: type(
                f"{cls.batch_name}{action.title().replace('_', '')}Batch",
                (UuidListSerializer,),
                {
                    "action": serializers.ChoiceField(choices=[action]),
                    **({"data": data()} if data else {}),
                },
            )
            for action, data in cls.batch_actions.items()
        }
        extend_schema(
            request=PolymorphicProxySerializer(
                component_name=f"{cls.batch_name}Batch",
                serializers=cls.batch_requests,
                resource_type_field_name="action",
            ),
            responses={200: cls.serializer_class(many=True), 204: None},
        )(cls)

    def post(self, request, *args, **kwargs):
        action = request.data.get("action") if isinstance(request.data, dict) else None
        # A list or an object for an action cannot even be looked up.
        if not isinstance(action, str) or action not in self.batch_requests:
            invalid = serializers.ChoiceField.default_error_messages["invalid_choice"]
            raise ValidationError({"action": [invalid.format(input=action)]})
        body = self.batch_requests[action](
            data=request.data, context=self.get_serializer_context()
        )
        body.is_valid(raise_exception=True)
        with transaction.atomic():
            rows = rows_in_order(self.get_queryset(), body.validated_data["uuids"])
            changed = getattr(self, f"perform_{action}")(
                rows, body.validated_data.get("data", {})
            )
        if changed is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(self.get_serializer(changed, many=True).data)


class ReorderView(GenericAPIView):
    """``POST /<collection>/reorder``: the rows in the order they should take.

    The body is ``{"uuids": [...]}``; a view whose order has a scope (the
    column a task list belongs to) extends ``UuidListSerializer`` with it.
    ``perform_reorder(rows, data)`` receives the rows in the order asked. A
    row deleted while the order was being applied refuses the request like
    one that was never there: the order asked for could not be given.
    """

    serializer_class = UuidListSerializer

    @extend_schema(responses={204: None})
    def post(self, request, *args, **kwargs):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        with transaction.atomic():
            rows = rows_in_order(self.get_queryset(), body.validated_data["uuids"])
            self.perform_reorder(rows, body.validated_data)
            refuse_if_gone(self.get_queryset(), rows)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AvailableActionsView(GenericAPIView):
    """``POST /<resources>/available-actions``: what the caller may do to rows.

    A read, sent as a POST because the list of uuids can be long. Every uuid
    asked comes back with a key; one the caller cannot reach comes back with
    no action, like one that does not exist, so a stale uuid does not cost
    the rest of the list its answer.

    ``get_available_actions(uuids)`` answers a dict of uuid to serialized
    actions, for the rows the caller can reach.
    """

    serializer_class = UuidListSerializer

    @extend_schema(
        responses={
            200: {
                "type": "object",
                "additionalProperties": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            name: {"type": "boolean" if name == "bulk" else "string"}
                            for name in ActionSerializer().fields
                        },
                        "required": list(ActionSerializer().fields),
                    },
                },
            }
        }
    )
    def post(self, request, *args, **kwargs):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        uuids = body.validated_data["uuids"]
        available = self.get_available_actions(uuids)
        return Response({str(uuid): available.get(uuid, []) for uuid in uuids})


class LinkView(GenericAPIView):
    """A yes/no link between the caller and a row: a pin, a favorite, a watch.

    ``PUT`` sets it and ``DELETE`` removes it; both answer 204 and repeating
    either changes nothing, so a request replayed after a network cut is
    harmless. A link that carries a value (watching or muted) names its
    serializer in ``serializer_class`` and receives the validated value.
    """

    serializer_class = None

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        extend_schema_view(
            put=extend_schema(request=cls.serializer_class, responses={204: None}),
            delete=extend_schema(request=None, responses={204: None}),
        )(cls)

    def put(self, request, *args, **kwargs):
        row = self.get_object()
        data = {}
        if self.serializer_class is not None:
            body = self.get_serializer(data=request.data)
            body.is_valid(raise_exception=True)
            data = body.validated_data
        self.perform_link(row, data)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def delete(self, request, *args, **kwargs):
        self.perform_unlink(self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)
