from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers, viewsets
from rest_framework.exceptions import ValidationError

from workspace.common.serializers import UuidListSerializer
from workspace.common.uuids import UUID_PATTERN
from workspace.common.views import (
    AvailableActionsView,
    BatchView,
    taken_value_refused,
)

from ..actions import actions_for
from ..filters import PersonFilter
from ..models import Person
from ..queries import reachable_list, user_persons
from ..serializers import (
    LINKED_USER_TAKEN,
    ListDataSerializer,
    PersonSerializer,
    book_of,
)
from ..services.lists import ScopeMismatch, add_members, remove_members
from ..services.persons import (
    create_person,
    delete_person,
    move_to_scope,
    update_person,
)


class PersonRowsMixin:
    """The contacts the caller can see and change: their own address book
    and the ones of their groups."""

    queryset = Person.objects.none()
    serializer_class = PersonSerializer

    def get_queryset(self):
        return (
            user_persons(self.request.user)
            .select_related("linked_user", "group")
            .order_by("display_name", "uuid")
        )


@extend_schema_view(
    list=extend_schema(tags=["People"], summary="List contacts"),
    create=extend_schema(tags=["People"], summary="Create a contact"),
    retrieve=extend_schema(tags=["People"], summary="Get a contact"),
    partial_update=extend_schema(
        tags=["People"],
        summary="Update a contact",
        description=(
            "Naming another `group` moves the contact to that address book, "
            "and takes it off the lists of the one it leaves."
        ),
    ),
    destroy=extend_schema(tags=["People"], summary="Delete a contact"),
)
class PersonViewSet(PersonRowsMixin, viewsets.ModelViewSet):
    filterset_class = PersonFilter
    lookup_field = "uuid"
    lookup_value_regex = UUID_PATTERN
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def perform_create(self, serializer):
        fields = dict(serializer.validated_data)
        book = book_of(None, fields, self.request.user)
        fields.pop("group", None)
        # The serializer checked the book holds no other contact for that
        # account; a concurrent write can still land in between.
        with taken_value_refused("linked_user", LINKED_USER_TAKEN):
            serializer.instance = create_person(**book, **fields)

    def perform_update(self, serializer):
        person = serializer.instance
        fields = dict(serializer.validated_data)
        book = book_of(person, fields, self.request.user)
        fields.pop("group", None)
        owner, group = book.get("owner"), book.get("group")
        with taken_value_refused("linked_user", LINKED_USER_TAKEN):
            if fields:
                update_person(person, **fields)
            if (owner, group) != (person.owner, person.group):
                move_to_scope(person, owner=owner, group=group)

    def perform_destroy(self, instance):
        delete_person(instance)


@extend_schema(tags=["People"], summary="Add or remove several contacts from a list")
class PersonBatchView(PersonRowsMixin, BatchView):
    batch_name = "Person"
    batch_actions = {
        "add_to_list": ListDataSerializer,
        "remove_from_list": ListDataSerializer,
    }

    def _list(self, data):
        """The contact list a batch names; one out of reach is refused like
        one that does not exist."""
        person_list = reachable_list(self.request.user, data["list"])
        if person_list is None:
            unknown = serializers.PrimaryKeyRelatedField.default_error_messages[
                "does_not_exist"
            ]
            raise ValidationError(
                {"data": {"list": [unknown.format(pk_value=data["list"])]}}
            )
        return person_list

    def perform_add_to_list(self, persons, data):
        try:
            add_members(self._list(data), persons)
        except ScopeMismatch:
            raise ValidationError(
                {
                    "uuids": [
                        _("Every person must be in the same address book as the list.")
                    ]
                }
            ) from None
        return persons

    def perform_remove_from_list(self, persons, data):
        remove_members(self._list(data), persons)
        return persons


@extend_schema(tags=["People"], summary="Get the actions available on contacts")
class PersonAvailableActionsView(PersonRowsMixin, AvailableActionsView):
    # The request names contacts; the mixin's serializer is a contact's.
    serializer_class = UuidListSerializer

    def get_available_actions(self, uuids):
        persons = self.get_queryset().filter(uuid__in=uuids)
        available = actions_for(self.request.user, persons)
        return {uuid: available[str(uuid)] for uuid in uuids if str(uuid) in available}
