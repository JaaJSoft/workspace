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
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.booleans import is_truthy
from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import parse_uuid_or_none

from ..actions import VaultActionRegistry
from ..models import VaultEntry, VaultRole
from ..queries import (
    accessible_entries_q,
    active_identity,
    get_vault_role,
    reachable_vault,
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
    """Whether the registry offers *action_id* on *entry* for *role*."""
    return VaultActionRegistry.is_action_available(
        action_id,
        user,
        entry,
        role=role,
        trashed=entry.deleted_at is not None,
        schema=schema_for(entry.type, default=()),
        # One entry, so one query: the batch shape the actions endpoint needs
        # would buy nothing here.
        present_fields=frozenset(entry.fields.values_list("field_id", flat=True)),
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


class EntryPurgeView(CacheControlMixin, APIView):
    """Destroy a trashed entry and its fields.

    Only from the trash: that step is the confirmation, and without it one
    mistyped URL destroys a live entry with nothing to undo it.

    Owner only, and read off what DeleteEntryForeverAction declares rather
    than written out again here. A key wrap opens a vault, and every other
    entry action follows from that - this one does not, because it is the
    only one no restore can undo.
    """

    cache_no_store = True

    @extend_schema(
        tags=["Vault"],
        summary="Delete a trashed entry for good",
        responses={204: None},
    )
    @sensitive_variables()
    def post(self, request, uuid):
        # Lighter than _reachable_entry: the row is about to be destroyed, so
        # only the vault comes along, for the role.
        entry = (
            VaultEntry.objects.filter(accessible_entries_q(request.user), uuid=uuid)
            .select_related("vault")
            .first()
        )
        if entry is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        role = get_vault_role(request.user, entry.vault)
        if role != VaultRole.OWNER:
            return Response(
                {"detail": "Only the vault owner may delete an entry for good."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not _offers("delete_forever", request.user, entry, role):
            return _not_in_the_trash()
        # Conditional, because the check above ran on a copy read outside any
        # lock: a restore landing in between would otherwise destroy an entry
        # the user has just been told is back.
        destroyed, _ = VaultEntry.objects.filter(
            pk=entry.pk, deleted_at__isnull=False
        ).delete()
        if not destroyed:
            return _not_in_the_trash()
        return Response(status=status.HTTP_204_NO_CONTENT)
