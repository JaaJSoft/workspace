from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.http_ranges import safe_filename
from workspace.common.uuids import parse_uuid_or_none

from ..queries import reachable_list, reachable_person, user_person_lists, user_persons
from ..serializers import parse_scope
from ..services.vcard import VCardError
from ..services.vcard_export import export_vcards
from ..services.vcard_import import import_vcards

IMPORT_MAX_SIZE = 20 * 1024 * 1024
VCARD_CONTENT_TYPE = "text/vcard; charset=utf-8"


def _vcf_response(text, filename):
    response = HttpResponse(text, content_type=VCARD_CONTENT_TYPE)
    response["Content-Disposition"] = (
        f'attachment; filename="{safe_filename(filename)}.vcf"'
    )
    return response


def _decode(data):
    """A vCard is UTF-8 by spec; a 2.1 export from an old phone may not be."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")


@extend_schema(tags=["People"])
class PersonImportView(APIView):
    parser_classes = [MultiPartParser]

    @extend_schema(
        summary="Import a vCard file",
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                    "scope": {
                        "type": "string",
                        "description": "`mine` or `group:<id>`.",
                    },
                },
                "required": ["file"],
            }
        },
        responses={
            200: OpenApiResponse(
                description="Counts of persons created and updated, and of lists."
            )
        },
    )
    def post(self, request):
        upload = request.FILES.get("file")
        if upload is None:
            return Response(
                {"file": ["No file provided."]}, status=status.HTTP_400_BAD_REQUEST
            )
        if upload.size > IMPORT_MAX_SIZE:
            return Response(
                {"file": ["File too large. Maximum size is 20 MB."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        scope = parse_scope(request.user, request.data.get("scope", "mine"))
        try:
            report = import_vcards(_decode(upload.read()), **scope)
        except VCardError:
            return Response(
                {"file": ["This is not a vCard file."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(report.as_dict())


def _uuid_param(request, name):
    """``(uuid, error)``: the parsed filter, or the 400 a malformed one earns."""
    raw = request.query_params.get(name)
    if not raw:
        return None, None
    value = parse_uuid_or_none(raw)
    if value is None:
        return None, Response(
            {name: [f"Malformed {name} uuid."]}, status=status.HTTP_400_BAD_REQUEST
        )
    return value, None


@extend_schema(tags=["People"])
class PersonExportView(APIView):
    """One endpoint, the same filters as the person list: the narrowest
    one given decides what the file holds and what it is called."""

    @extend_schema(
        summary="Export contacts as vCard",
        parameters=[
            OpenApiParameter("person", str, description="One contact."),
            OpenApiParameter("list", str, description="One list and its members."),
            OpenApiParameter(
                "scope",
                str,
                description="`mine` or `group:<id>`; every reachable contact when omitted.",
            ),
        ],
        responses={
            200: OpenApiResponse(description="A .vcf file"),
            400: None,
            404: None,
        },
    )
    def get(self, request):
        person_uuid, error = _uuid_param(request, "person")
        if error is not None:
            return error
        if person_uuid is not None:
            person = reachable_person(request.user, person_uuid)
            if person is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
            return _vcf_response(export_vcards([person]), person.display_name)

        list_uuid, error = _uuid_param(request, "list")
        if error is not None:
            return error
        if list_uuid is not None:
            person_list = reachable_list(request.user, list_uuid)
            if person_list is None:
                return Response(status=status.HTTP_404_NOT_FOUND)
            text = export_vcards(person_list.members.all(), [person_list])
            return _vcf_response(text, person_list.name)

        persons = user_persons(request.user)
        lists = user_person_lists(request.user)
        filename = "contacts"
        raw_scope = request.query_params.get("scope")
        if raw_scope:
            scope = parse_scope(request.user, raw_scope)
            persons = persons.filter(**scope)
            lists = lists.filter(**scope)
            group = scope.get("group")
            filename = group.name if group is not None else "my-contacts"
        text = export_vcards(persons, lists.prefetch_related("members"))
        return _vcf_response(text, filename)
