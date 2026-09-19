from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from workspace.common.uuids import parse_uuid_or_none

from ..queries import user_persons
from ..serializers import PersonSerializer, parse_scope
from ..services.persons import delete_person


@extend_schema_view(
    list=extend_schema(
        tags=["People"],
        summary="List persons",
        parameters=[
            OpenApiParameter("q", str, description="Search in names, emails, phones."),
            OpenApiParameter("scope", str, description="`mine` or `group:<id>`."),
            OpenApiParameter("list", str, description="Only members of this list."),
        ],
    ),
    create=extend_schema(tags=["People"], summary="Create a person"),
    retrieve=extend_schema(tags=["People"], summary="Get a person"),
    partial_update=extend_schema(tags=["People"], summary="Update a person"),
    destroy=extend_schema(tags=["People"], summary="Delete a person"),
)
class PersonViewSet(viewsets.ModelViewSet):
    serializer_class = PersonSerializer
    lookup_field = "uuid"
    lookup_value_regex = "[0-9a-fA-F-]{36}"
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = user_persons(self.request.user).select_related("linked_user")
        params = self.request.query_params
        q = params.get("q", "").strip().lower()
        if q:
            qs = qs.filter(search_text__contains=q)
        scope = params.get("scope")
        if scope:
            try:
                qs = qs.filter(**parse_scope(self.request.user, scope))
            except ValidationError:
                raise ValidationError({"scope": "Unknown scope."}) from None
        raw_list = params.get("list")
        if raw_list:
            list_uuid = parse_uuid_or_none(raw_list)
            if list_uuid is None:
                raise ValidationError({"list": "Malformed list uuid."})
            qs = qs.filter(lists__uuid=list_uuid)
        return qs

    def perform_destroy(self, instance):
        delete_person(instance)
