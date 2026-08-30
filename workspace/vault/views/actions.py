"""What the caller may do with each row in a batch.

The rule that shapes this endpoint: one unreachable UUID must not cost the
other 199 their answer. A row in a vault the caller cannot open and a UUID
that names nothing both come back as an empty list inside a 200, so the
client reads every key it submitted without checking for holes. The projects
endpoint this is modelled on 404s the whole batch instead.

Two kinds of row are addressed here, chosen by ``target``. A second endpoint
would have duplicated the batch parsing, the spelling map and the
never-404 rule - three things that must not drift apart.
"""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import (
    BatchTooLarge,
    MalformedUuid,
    UuidBatchError,
    parse_uuid_batch,
)

from ..actions import VaultActionRegistry, VaultTargetActionRegistry
from ..models import Vault, VaultEntry
from ..queries import accessible_entries_q, user_vault_ids, vault_roles
from ..types import schema_for

MAX_BATCH = 200
TARGETS = frozenset({"entry", "vault"})


def _refused(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["Vault"],
    summary="Get available actions for vault entries or vaults",
    description=(
        "Return the actions the caller may take on each of a batch of UUIDs. "
        "`target` selects what those UUIDs name: entries (the default) or "
        "vaults. Every submitted UUID gets a key; one the caller cannot "
        "reach gets an empty list."
    ),
    request={
        "application/json": {
            "type": "object",
            "properties": {
                "uuids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                },
                "target": {"type": "string", "enum": sorted(TARGETS)},
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
        # Read through a mapping check rather than with .get: a JSON array or
        # scalar body hands the view a list or an int, and asking either for a
        # key is a 500 where the batch parser below already answers 400.
        data = request.data if isinstance(request.data, dict) else {}
        target = data.get("target", "entry")
        # isinstance before membership: a JSON array or object as `target` is
        # unhashable, and asking a frozenset about it raises TypeError - a 500
        # where the same malformed body deserves the 400 below.
        if not isinstance(target, str) or target not in TARGETS:
            return _refused(
                "target must be one of: " + ", ".join(sorted(TARGETS)) + "."
            )

        # The wording is chosen here from the kind of failure, never taken
        # from the exception: an exception's text is a path from the server's
        # internals to a response body.
        try:
            parsed = parse_uuid_batch(request.data, max_items=MAX_BATCH)
        except BatchTooLarge:
            return _refused(f"Too many UUIDs (max {MAX_BATCH}).")
        except MalformedUuid:
            return _refused("Malformed UUID in uuids.")
        except UuidBatchError:
            return _refused("uuids must be a non-empty list.")

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

        if target == "vault":
            return Response(
                self._vault_actions(request.user, parsed, spellings, result)
            )

        entries = (
            VaultEntry.objects.filter(
                accessible_entries_q(request.user), uuid__in=parsed
            )
            .select_related("vault")
            .prefetch_related("fields")
        )
        entries = list(entries)
        # What each row carries, resolved with the batch rather than per
        # action: an action asking for it would put back the per-row query
        # the registry's purity rule exists to prevent. encrypted_name and
        # encrypted_notes live on the row, not in EntryField, so they are
        # absent here - and no action requires them.
        present = {
            entry.uuid: frozenset(field.field_id for field in entry.fields.all())
            for entry in entries
        }

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
                present_fields=present[entry.uuid],
            )
            for key in spellings[entry.uuid]:
                result[key] = actions
        return Response(result)

    def _vault_actions(self, user, parsed, spellings, result):
        """The same answer shape, for vaults rather than entries.

        A vault carries no trash flag, no field schema and no stored fields,
        so its registry asks for none of the three - which is why the two
        registries are siblings rather than one with optional parameters.
        """
        reachable = set(user_vault_ids(user))
        vaults = Vault.objects.filter(
            uuid__in=[value for value in parsed if value in reachable]
        )
        vaults = list(vaults)
        roles = vault_roles(user, {vault.uuid for vault in vaults})
        for vault in vaults:
            role = roles.get(vault.uuid)
            if role is None:
                continue
            actions = VaultTargetActionRegistry.get_available_actions(
                user, vault, role=role
            )
            for key in spellings[vault.uuid]:
                result[key] = actions
        return result
