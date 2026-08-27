"""What the caller may do with each entry in a batch.

The one rule that shapes this endpoint: it answers nothing about existence.
An entry in a vault the caller cannot open and a UUID that names no row both
come back as an empty list inside a 200 - never a 404, never a missing key.
The projects endpoint this is modelled on does answer 404, and copying that
would turn this one into an oracle a caller could ask whether an entry
exists.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import parse_uuid_or_none

from ..actions import VaultActionRegistry
from ..models import VaultEntry
from ..queries import accessible_entries_q, get_vault_role
from ..types import schema_for

MAX_BATCH = 200


@extend_schema(
    tags=["Vault"],
    summary="Get available actions for vault entries",
    description=(
        "Return the actions the caller may take on each of a batch of entry "
        "UUIDs. Every submitted UUID gets a key; one the caller cannot reach "
        "gets an empty list."
    ),
    request={
        "application/json": {
            "type": "object",
            "properties": {
                "uuids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                },
            },
            "required": ["uuids"],
        },
    },
    responses={
        200: OpenApiResponse(
            response=OpenApiTypes.OBJECT,
            description="Map of UUID to the list of available actions.",
        ),
        400: OpenApiResponse(description="Malformed or oversized batch."),
    },
)
class VaultActionsView(CacheControlMixin, APIView):
    cache_no_store = True

    def post(self, request):
        uuids = request.data.get("uuids", [])
        if not isinstance(uuids, list) or not uuids:
            return Response(
                {"detail": "uuids must be a non-empty list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(uuids) > MAX_BATCH:
            return Response(
                {"detail": f"Too many UUIDs (max {MAX_BATCH})."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        parsed = []
        for item in uuids:
            value = parse_uuid_or_none(item)
            if value is None:
                return Response(
                    {"detail": "Malformed UUID in uuids."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            parsed.append(value)

        # Every UUID starts with an empty list, so an unreachable entry and a
        # UUID naming nothing produce the same answer without a branch that
        # could ever tell them apart.
        result = {str(value): [] for value in parsed}

        entries = VaultEntry.objects.filter(
            accessible_entries_q(request.user), uuid__in=parsed
        ).select_related("vault")

        # One role resolution per distinct vault, then pure in-memory
        # evaluation - the registry contract forbids queries inside actions.
        roles = {}
        for entry in entries:
            if entry.vault_id not in roles:
                roles[entry.vault_id] = get_vault_role(request.user, entry.vault)
            role = roles[entry.vault_id]
            if role is None:
                continue
            result[str(entry.uuid)] = VaultActionRegistry.get_available_actions(
                request.user,
                entry,
                role=role,
                trashed=entry.deleted_at is not None,
                schema=schema_for(entry.type),
            )
        return Response(result)
