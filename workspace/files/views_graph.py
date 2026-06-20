"""Standalone endpoint: a node/edge graph of files + their FileLink edges."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
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
        OpenApiParameter(
            name="search",
            type=OpenApiTypes.STR,
            description="Keep only nodes whose name matches (case-insensitive substring).",
        ),
        OpenApiParameter(
            name="exclude_descendants_of",
            type=OpenApiTypes.UUID,
            description="Drop the subtree of this folder UUID from the results.",
        ),
        OpenApiParameter(
            name="favorites",
            type=OpenApiTypes.BOOL,
            description="true -> only favorites; false -> only non-favorites.",
        ),
        OpenApiParameter(
            name="tags",
            type=OpenApiTypes.STR,
            description=(
                "Comma-separated tag UUIDs; keep only nodes carrying at least "
                "one of them (OR)."
            ),
        ),
    ],
)
class FileGraphView(APIView):
    permission_classes = [IsAuthenticated]

    def _uuid_param(self, request, name):
        """Parse a UUID query param; 400 on a malformed value, None when absent."""
        raw = request.query_params.get(name)
        if not raw:
            return None
        value = parse_uuid_or_none(raw)
        if value is None:
            raise ValidationError({name: "Must be a valid UUID."})
        return value

    def _tags_param(self, request):
        """Parse the comma-separated ``tags`` filter into a list of UUIDs.

        Returns None when absent. A collection filter, so a malformed UUID is a
        client bug -> 400 (per the query-param parsing contract)."""
        raw = request.query_params.get("tags")
        if not raw:
            return None
        uuids = []
        for part in raw.split(","):
            part = part.strip()
            if not part:
                continue
            value = parse_uuid_or_none(part)
            if value is None:
                raise ValidationError({"tags": "Must be a comma-separated list of UUIDs."})
            uuids.append(value)
        return uuids or None

    def get(self, request):
        scope = request.query_params.get("scope", "mine")
        if scope not in _VALID_SCOPES:
            raise ValidationError({"scope": "Must be 'mine' or 'all'."})
        fav_raw = request.query_params.get("favorites")
        data = build_file_graph(
            request.user,
            scope=scope,
            file_type=request.query_params.get("type") or None,
            under=self._uuid_param(request, "under"),
            exclude_descendants_of=self._uuid_param(request, "exclude_descendants_of"),
            favorites=is_truthy(fav_raw) if fav_raw else None,
            search=request.query_params.get("search") or None,
            tags=self._tags_param(request),
        )
        return Response(data)
