"""What the caller may do with each entry in a batch.

The rule that shapes this endpoint: one unreachable UUID must not cost the
other 199 their answer. An entry in a vault the caller cannot open and a UUID
that names no row both come back as an empty list inside a 200, so the client
reads every key it submitted without checking for holes. The projects
endpoint this is modelled on 404s the whole batch instead.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import UuidBatchError, parse_uuid_batch

from ..actions import VaultActionRegistry
from ..models import VaultEntry
from ..queries import accessible_entries_q, vault_roles
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
        try:
            parsed = parse_uuid_batch(request.data, max_items=MAX_BATCH)
        except UuidBatchError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Keyed by the spelling the caller sent, not by str(UUID): a client
        # that sent an uppercase or braced UUID reads back data[whatItSent],
        # and str() would answer under the canonical form instead.
        spellings = {}
        for item, value in zip(request.data["uuids"], parsed, strict=True):
            spellings.setdefault(value, []).append(str(item))
        # Every UUID starts with an empty list, so an unreachable entry and a
        # UUID naming nothing produce the same answer without a branch that
        # could ever tell them apart.
        result = {key: [] for keys in spellings.values() for key in keys}

        entries = VaultEntry.objects.filter(
            accessible_entries_q(request.user), uuid__in=parsed
        ).select_related("vault")
        entries = list(entries)

        # Every role in two queries, then pure in-memory evaluation: asking
        # per vault would put back the per-row shape the registry avoids.
        roles = vault_roles(request.user, {entry.vault_id for entry in entries})
        for entry in entries:
            # A miss means a wrap was revoked between the two queries -
            # accessible_entries_q has already excluded every other case.
            role = roles.get(entry.vault_id)
            if role is None:
                continue
            actions = VaultActionRegistry.get_available_actions(
                request.user,
                entry,
                role=role,
                trashed=entry.deleted_at is not None,
                schema=schema_for(entry.type, default=()),
            )
            for key in spellings[entry.uuid]:
                result[key] = actions
        return Response(result)
