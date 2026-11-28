from django.db.models import Count
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView

from workspace.common.exceptions import Conflict
from workspace.common.views import LinkView, taken_value_refused

from ..filters import PersonListFilter
from ..models import PersonList
from ..queries import reachable_list, reachable_person, user_person_lists
from ..serializers import PersonListSerializer, book_of
from ..services.lists import (
    ScopeMismatch,
    add_members,
    create_list,
    remove_members,
    rename_list,
)


class PersonListRowsMixin:
    """The contact lists the caller can see and change: the ones of their
    own address book and of their groups'."""

    queryset = PersonList.objects.none()
    serializer_class = PersonListSerializer
    lookup_field = "uuid"
    name_taken = gettext_lazy("A list with this name already exists.")

    def get_queryset(self):
        return (
            user_person_lists(self.request.user)
            .select_related("group")
            .annotate(member_count=Count("members"))
            .order_by("name", "uuid")
        )


@extend_schema(tags=["People"])
@extend_schema_view(
    get=extend_schema(summary="List contact lists"),
    post=extend_schema(summary="Create a contact list"),
)
class PersonListListView(PersonListRowsMixin, ListCreateAPIView):
    filterset_class = PersonListFilter
    # A person's contact lists, listed whole.
    pagination_class = None

    def perform_create(self, serializer):
        fields = serializer.validated_data
        book = book_of(None, fields, self.request.user)
        with taken_value_refused("name", self.name_taken):
            person_list = create_list(**book, name=fields["name"])
        person_list.member_count = 0
        serializer.instance = person_list


@extend_schema(tags=["People"])
@extend_schema_view(
    get=extend_schema(summary="Get a contact list"),
    patch=extend_schema(summary="Rename a contact list"),
    delete=extend_schema(
        summary="Delete a contact list", description="Its members stay contacts."
    ),
)
class PersonListDetailView(PersonListRowsMixin, RetrieveUpdateDestroyAPIView):
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def perform_update(self, serializer):
        person_list = serializer.instance
        name = serializer.validated_data.get("name")
        if name is not None and name != person_list.name:
            with taken_value_refused("name", self.name_taken):
                rename_list(person_list, name)


@extend_schema(tags=["People"])
@extend_schema_view(
    put=extend_schema(summary="Put a contact on a list"),
    delete=extend_schema(summary="Take a contact off a list"),
)
class PersonListMemberView(LinkView):
    """Whether one contact is on one list. Either of the two out of reach
    answers like one that does not exist."""

    def get_object(self):
        user = self.request.user
        person_list = reachable_list(user, self.kwargs["list_uuid"])
        person = reachable_person(user, self.kwargs["uuid"])
        if person_list is None or person is None:
            raise NotFound
        return person_list, person

    def perform_link(self, pair, data):
        person_list, person = pair
        try:
            add_members(person_list, [person])
        except ScopeMismatch:
            raise Conflict(
                _("Every person must be in the same address book as the list.")
            ) from None

    def perform_unlink(self, pair):
        person_list, person = pair
        remove_members(person_list, [person])
