"""Entries, and the five places a write could reach across a vault boundary.

An entry, its tag set and its complete field set travel in one request and are
written in one transaction, because one signature covers all three. A write
split in two would leave a stored signature matching nothing, and the next
client to open the entry would read a legitimate half-write as tampering.

PUT rather than PATCH on a member: every signed field travels on every write,
so there is no partial update to express.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import (
    BatchTooLarge,
    MalformedUuid,
    UuidBatchError,
    parse_uuid_batch,
    parse_uuid_or_none,
)

from ..actions import VaultActionRegistry
from ..models import VaultEntry, VaultRole
from ..queries import (
    accessible_entries_q,
    active_identity,
    get_vault_role,
    reachable_vault,
    vault_roles,
)
from ..serializers import VaultEntrySerializer, VaultEntryWriteSerializer
from ..services.attestation import AttestationError
from ..services.entries import (
    UnknownFolder,
    UnknownTag,
    entry_queryset,
    entry_signature_payload,
    resolve_folder,
    resolve_tags,
    write_entry,
)
from ..services.metadata import verify_record
from ..types import schema_for

SENSITIVE_BODY_FIELDS = (
    "encrypted_name",
    "encrypted_notes",
    "fields",
    "metadata_sig",
)


def _reachable_entry(user, uuid):
    """The entry *user* may touch, or None - never saying which reason.

    One lookup behind get, put, delete and restore, so that invariant holds in
    one place. Purge runs a lighter query of its own, and says so there.
    """
    return entry_queryset().filter(accessible_entries_q(user), uuid=uuid).first()


def _not_in_the_trash():
    return Response(
        {"detail": "The entry is not in the trash."},
        status=status.HTTP_409_CONFLICT,
    )


def _not_the_owner():
    return Response(
        {"detail": "Only the vault owner may permanently delete an entry."},
        status=status.HTTP_403_FORBIDDEN,
    )


def _refused(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


def _signature_refused():
    return Response(
        {"detail": "The entry metadata signature does not verify."},
        status=status.HTTP_400_BAD_REQUEST,
    )


class _EntryWriteMixin:
    def _write(self, request, data, *, existing=None):
        """Build, verify and store the entry, or return the refusal Response.

        Nothing touches the database until the signature verifies: the payload
        is built from an unsaved instance carrying exactly the columns that are
        about to be written.
        """
        identity = active_identity(request.user)
        if identity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        vault = reachable_vault(request.user, data["vault"])
        if vault is None or (existing is not None and existing.vault_id != vault.pk):
            return Response(status=status.HTTP_404_NOT_FOUND)

        # The wording is chosen here from the kind of failure, never taken from
        # the exception: an exception's text is a path from the server's
        # internals to a response body.
        try:
            folder = resolve_folder(request.user, vault, data["folder"])
        except UnknownFolder:
            return Response(
                {"detail": "The folder does not exist in this vault."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            tags = resolve_tags(request.user, vault, data["tags"])
        except UnknownTag:
            return Response(
                {"detail": "A tag does not exist in this vault."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        entry = existing or VaultEntry(uuid=data["uuid"], vault=vault)
        if existing is None:
            # The generation the vault key is on right now, never the column
            # default: an entry created after a rotation would otherwise sign a
            # key generation it was not encrypted under.
            entry.key_version = vault.key_version
        entry.type = data["type"]
        entry.folder = folder
        entry.is_favorite = data["is_favorite"]
        entry.encrypted_name = data["encrypted_name"]
        entry.encrypted_notes = data["encrypted_notes"]
        entry.metadata_sig = data["metadata_sig"]

        payload = entry_signature_payload(
            entry,
            signer_account_uuid=identity.uuid,
            tag_uuids=[tag.uuid for tag in tags],
            fields=data["fields"],
        )
        try:
            verify_record(payload, identity.sig_public, data["metadata_sig"])
        except AttestationError:
            return _signature_refused()

        try:
            with transaction.atomic():
                if existing is None:
                    # force_insert so a client-supplied UUID that already names
                    # a row - possibly someone else's - collides rather than
                    # overwriting it.
                    entry.save(force_insert=True)
                write_entry(entry, tags=tags, fields=data["fields"])
        except ValidationError:
            # resolve_folder already scoped the folder to the vault, so clean()
            # has nothing left to reject; this stays as a floor, and says so
            # without handing back the validator's own text.
            return Response(
                {"detail": "The entry could not be validated."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except IntegrityError:
            return Response(status=status.HTTP_409_CONFLICT)

        entry = entry_queryset().get(uuid=entry.uuid)
        return Response(
            VaultEntrySerializer(entry).data,
            status=(
                status.HTTP_200_OK if existing is not None else status.HTTP_201_CREATED
            ),
        )


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class EntryListView(_EntryWriteMixin, CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        tags=["Vault"],
        summary="List the entries of one vault",
        parameters=[
            OpenApiParameter("vault", str, required=True, description="Vault UUID"),
            OpenApiParameter("trashed", bool, description="Return the trash instead"),
        ],
        responses=VaultEntrySerializer(many=True),
    )
    @sensitive_variables()
    def get(self, request):
        vault_uuid = parse_uuid_or_none(request.query_params.get("vault"))
        if vault_uuid is None:
            return Response(
                {"detail": "A well-formed vault UUID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        vault = reachable_vault(request.user, vault_uuid)
        if vault is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        entries = entry_queryset().filter(
            accessible_entries_q(request.user), vault=vault
        )
        # is_truthy, never Python truthiness: '?trashed=false' is a non-empty
        # string and would otherwise enable the very filter it asks to disable.
        if is_truthy(request.query_params.get("trashed")):
            entries = entries.exclude(deleted_at__isnull=True)
        else:
            entries = entries.filter(deleted_at__isnull=True)
        return Response(VaultEntrySerializer(entries, many=True).data)

    @extend_schema(
        tags=["Vault"],
        summary="Create an entry with its fields and tags",
        request=VaultEntryWriteSerializer,
        responses={201: VaultEntrySerializer},
    )
    @sensitive_variables()
    def post(self, request):
        serializer = VaultEntryWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return self._write(request, serializer.validated_data)


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class EntryDetailView(_EntryWriteMixin, CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        tags=["Vault"], summary="Read one entry", responses={200: VaultEntrySerializer}
    )
    @sensitive_variables()
    def get(self, request, uuid):
        entry = _reachable_entry(request.user, uuid)
        if entry is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(VaultEntrySerializer(entry).data)

    @extend_schema(
        tags=["Vault"],
        summary="Replace an entry, its fields and its tags",
        request=VaultEntryWriteSerializer,
        responses={200: VaultEntrySerializer},
    )
    @sensitive_variables()
    def put(self, request, uuid):
        serializer = VaultEntryWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["uuid"] != uuid:
            return Response(
                {"detail": "The body names another entry."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        entry = _reachable_entry(request.user, uuid)
        if entry is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return self._write(request, data, existing=entry)

    @extend_schema(
        tags=["Vault"], summary="Move an entry to the trash", responses={204: None}
    )
    def delete(self, request, uuid):
        entry = _reachable_entry(request.user, uuid)
        if entry is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Soft: the trash is a view, not a rewrite. metadata_sig is untouched
        # because deleted_at is not inside it, and the server may not re-sign.
        entry.deleted_at = timezone.now()
        entry.save(update_fields=["deleted_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


def _offers(action_id, user, entry, role):
    """Whether the registry offers *action_id* on *entry* for *role*.

    Only the action that declares it gets present_fields, because building
    it means reading the entry's field rows: restore and delete_forever
    never look, and a whole trash destroyed in one call would otherwise pull
    every ciphertext it holds to answer a question nobody asked. The others
    get None rather than an empty set, so an action that grew a use for it
    without declaring one raises instead of quietly hiding itself.
    """
    action = VaultActionRegistry.get(action_id)
    reads_fields = action is not None and action.reads_present_fields
    return VaultActionRegistry.is_action_available(
        action_id,
        user,
        entry,
        role=role,
        trashed=entry.deleted_at is not None,
        schema=schema_for(entry.type, default=()),
        # Iterating the manager rather than values_list, which would ignore a
        # prefetched cache and query again: every caller that gets here has
        # the rows already, for the serializer it is about to build.
        present_fields=(
            frozenset(field.field_id for field in entry.fields.all())
            if reads_fields
            else None
        ),
    )


class EntryRestoreView(CacheControlMixin, APIView):
    """Take an entry back out of the trash.

    No signature travels: deleted_at is not inside the signed payload, so
    there is nothing for the client to re-sign and nothing for the server to
    verify. Idempotent, so a retried request after a lost answer is not an
    error - which is why an entry that is already live is answered rather
    than refused, though the registry offers "restore" only in the trash.
    """

    cache_no_store = True

    @extend_schema(
        tags=["Vault"],
        summary="Restore an entry from the trash",
        request=None,
        responses={200: VaultEntrySerializer},
    )
    @sensitive_variables()
    def post(self, request, uuid):
        entry = _reachable_entry(request.user, uuid)
        if entry is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if entry.deleted_at is not None:
            role = get_vault_role(request.user, entry.vault)
            if not _offers("restore", request.user, entry, role):
                return Response(
                    {"detail": "Restoring this entry is not available to you."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            entry.deleted_at = None
            entry.save(update_fields=["deleted_at", "updated_at"])
        return Response(VaultEntrySerializer(entry).data)


MAX_PURGE_BATCH = 200


class _TrashChanged(Exception):
    """A row named by the batch left the trash before the delete ran."""


@extend_schema(
    tags=["Vault"],
    summary="Destroy trashed entries in one call",
    description=(
        "Destroy every named trashed entry, or none of them. Send exactly "
        "one of `uuids` (an explicit selection, at most 200) or `vault` "
        "(every trashed entry of that vault, uncapped). Owner only."
    ),
    request={
        # Two branches rather than one object with two optional keys: a body
        # naming both matches both branches and a body naming neither matches
        # none, which is how oneOf spells the exactly-one the view enforces.
        "application/json": {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {
                        "uuids": {
                            "type": "array",
                            "items": {"type": "string", "format": "uuid"},
                            "minItems": 1,
                            "maxItems": MAX_PURGE_BATCH,
                        },
                    },
                    "required": ["uuids"],
                },
                {
                    "type": "object",
                    "properties": {"vault": {"type": "string", "format": "uuid"}},
                    "required": ["vault"],
                },
            ],
        },
    },
    responses={
        200: OpenApiResponse(
            response={
                "type": "object",
                "properties": {
                    "destroyed": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                    },
                },
                "required": ["destroyed"],
            },
            description="The UUIDs that were destroyed.",
        ),
        400: OpenApiResponse(description="Malformed, oversized or ambiguous body."),
        403: OpenApiResponse(description="Not the owner of the vault."),
        404: OpenApiResponse(description="A named entry or vault is out of reach."),
        409: OpenApiResponse(description="A named entry is not in the trash."),
    },
)
class EntryBatchPurgeView(CacheControlMixin, APIView):
    """Destroy a set of trashed entries, all of them or none.

    The single-entry endpoint above cannot be looped over safely: N requests
    fail in pieces, half destroyed and half in place, and there is then
    nothing true left to tell the user. Everything here runs in one
    transaction for that reason, and the answer names what went.

    Two bodies rather than one. A selection is a list of UUIDs, capped the
    way the actions endpoint is, because it comes from rows on screen. A
    whole trash is named by its vault and is *not* capped: capping it would
    put the client back in the business of slicing, which is the loop this
    endpoint exists to remove.
    """

    cache_no_store = True

    @sensitive_variables()
    def post(self, request):
        # Read through a mapping check rather than with .get: a JSON array or
        # scalar body hands the view a list or an int, and asking either for
        # a key is a 500 where this deserves a 400.
        data = request.data if isinstance(request.data, dict) else {}
        named_uuids = "uuids" in data
        named_vault = "vault" in data
        if named_uuids == named_vault:
            return _refused("Send exactly one of uuids or vault.")

        # The whole request runs in the transaction, reads included. Outside
        # it the rows are read under no lock, and `delete()` takes the
        # collector path on a model with cascades: the final DELETE names
        # primary keys with no deleted_at predicate on it, so a restore
        # committing between the read and that statement would be destroyed
        # whatever the read had seen.
        try:
            with transaction.atomic():
                if named_vault:
                    outcome = self._whole_trash(request.user, data["vault"])
                else:
                    outcome = self._selection(request.user, data)
                if isinstance(outcome, Response):
                    return outcome
                entries, doomed = outcome

                # One query for every role, then pure in-memory checks:
                # asking per row would make a 200-row batch cost 200 lookups.
                roles = vault_roles(request.user, {entry.vault_id for entry in entries})
                for entry in entries:
                    role = roles.get(entry.vault_id)
                    if role != VaultRole.OWNER:
                        return _not_the_owner()
                    # Read off the registry rather than restated here, so the
                    # batch and the single-entry endpoint cannot drift on who
                    # may destroy what.
                    if not _offers("delete_forever", request.user, entry, role):
                        return _not_in_the_trash()

                targets = [entry.uuid for entry in entries]
                # The count still guards, because the lock above is a no-op on
                # SQLite - it holds the whole database for the transaction
                # instead - and because a queryset delete on a model with
                # cascades cannot carry the predicate itself.
                #
                # Short, not different: a row that left the trash under us is
                # the accident this catches. The whole-trash form names a
                # filter, so a row someone trashed while it ran is destroyed
                # too and the count comes back long - which is what emptying a
                # trash means, not a reason to refuse the whole call.
                _, per_model = doomed.delete()
                if per_model.get(VaultEntry._meta.label, 0) < len(targets):
                    raise _TrashChanged
        except _TrashChanged:
            return _not_in_the_trash()
        return Response({"destroyed": [str(value) for value in targets]})

    def _whole_trash(self, user, vault_uuid):
        """One vault's trash: the rows to check, and the set to destroy.

        The set is a filter rather than the UUIDs of the rows - a trash is
        uncapped, and naming its rows one by one would put a bind parameter
        per entry into the DELETE, which a large enough trash turns into a
        500 instead of an empty trash.
        """
        parsed = parse_uuid_or_none(vault_uuid)
        vault = None if parsed is None else reachable_vault(user, parsed)
        if vault is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Checked here as well as in the loop the caller runs, because an
        # empty trash never reaches that loop - and "you are not the owner"
        # must not turn into a 200 just because there was nothing to destroy.
        if get_vault_role(user, vault) != VaultRole.OWNER:
            return _not_the_owner()
        trashed = VaultEntry.objects.filter(vault=vault, deleted_at__isnull=False)
        # Neither the join nor the field rows: the role is resolved from
        # vault_id below, delete_forever reads no field, and select_for_update
        # would take the joined vault row with it - locking the vault itself
        # for as long as its trash takes to empty.
        entries = list(trashed.select_for_update())
        return entries, trashed

    def _selection(self, user, data):
        """The named entries and the set to destroy, or the refusal."""
        # The wording is chosen here from the kind of failure, never taken
        # from the exception: an exception's text is a path from the server's
        # internals to a response body.
        try:
            parsed = parse_uuid_batch(data, max_items=MAX_PURGE_BATCH)
        except BatchTooLarge:
            return _refused(f"Too many UUIDs (max {MAX_PURGE_BATCH}).")
        except MalformedUuid:
            return _refused("Malformed UUID in uuids.")
        except UuidBatchError:
            return _refused("uuids must be a non-empty list.")
        wanted = set(parsed)
        entries = list(
            VaultEntry.objects.filter(
                accessible_entries_q(user), uuid__in=wanted
            ).select_for_update()
        )
        if {entry.uuid for entry in entries} != wanted:
            # Nothing destroyed, and the answer keeps the silence the
            # single-entry endpoint keeps: "no such entry" and "not yours"
            # are one answer.
            return Response(status=status.HTTP_404_NOT_FOUND)
        # Bounded by MAX_PURGE_BATCH, so naming the rows costs a bind
        # parameter each and no more.
        return entries, VaultEntry.objects.filter(
            uuid__in=wanted, deleted_at__isnull=False
        )
