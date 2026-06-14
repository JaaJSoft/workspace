"""Standalone endpoint: a node/edge graph of files + their FileLink edges."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none

from .services.graph import build_file_graph

_VALID_SCOPES = {"mine", "all"}


@extend_schema(
    tags=["Files"],
    summary="File link graph",
    description=(
        "Return {nodes, edges} for the files the user can see. scope=mine "
        "(own files) or all (owned/group/shared). Optional type filter (e.g. "
        "markdown for the notes graph). Edges are FileLink rows whose source "
        "and target are both in the node set."
    ),
    parameters=[
        OpenApiParameter(
            name="scope", type=OpenApiTypes.STR, description="mine | all (default mine)"
        ),
        OpenApiParameter(
            name="type",
            type=OpenApiTypes.STR,
            description="Restrict nodes to this file type.",
        ),
        OpenApiParameter(
            name="under",
            type=OpenApiTypes.UUID,
            description="Restrict nodes to the subtree of this folder UUID.",
        ),
    ],
)
class FileGraphView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = request.query_params.get("scope", "mine")
        if scope not in _VALID_SCOPES:
            raise ValidationError({"scope": "Must be 'mine' or 'all'."})
        file_type = request.query_params.get("type") or None
        under_raw = request.query_params.get("under")
        under = None
        if under_raw:
            under = parse_uuid_or_none(under_raw)
            if under is None:
                raise ValidationError({"under": "Must be a valid UUID."})
        data = build_file_graph(
            request.user, scope=scope, file_type=file_type, under=under
        )
        return Response(data)
