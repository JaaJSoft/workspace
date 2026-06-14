"""Standalone endpoint: a node/edge graph of files + their FileLink edges."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
    ],
)
class FileGraphView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        scope = request.query_params.get("scope", "mine")
        if scope not in _VALID_SCOPES:
            raise ValidationError({"scope": "Must be 'mine' or 'all'."})
        file_type = request.query_params.get("type") or None
        data = build_file_graph(request.user, scope=scope, file_type=file_type)
        return Response(data)
