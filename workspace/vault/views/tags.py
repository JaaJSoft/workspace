"""Tags, the flat half of a vault's structure.

A tag's colour is plaintext, so metadata_sig is the only thing covering it -
the server rebuilds the payload from the columns it is about to write and
refuses anything else.

Deleting one is a transaction: the entries that carry it arrive re-signed
without it, and the server writes only signatures it has verified. It never
signs on a client's behalf.
"""

from django.db import IntegrityError, transaction
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import parse_uuid_or_none

from ..models import VaultEntry, VaultTag
from ..queries import active_identity, reachable_vault, visible_tags
from ..serializers import (
    TagDeleteSerializer,
    VaultTagSerializer,
    VaultTagWriteSerializer,
)
from ..services.attestation import AttestationError
from ..services.entries import entry_signature_payload
from ..services.metadata import tag_metadata_payload, verify_record
from .folders import _ContentsChanged, _SignatureRefused

SENSITIVE_BODY_FIELDS = ("encrypted_name", "metadata_sig")


def _signature_refused():
    return Response(
        {"detail": "The tag metadata signature does not verify."},
        status=status.HTTP_400_BAD_REQUEST,
    )


class _TagWriteMixin:
    def _verified(self, request, data, *, tag=None):
        """``(vault, None)`` or ``(None, Response)`` to return as-is."""
        identity = active_identity(request.user)
        if identity is None:
            return None, Response(status=status.HTTP_404_NOT_FOUND)
        vault = reachable_vault(request.user, data["vault"])
        if vault is None or (tag is not None and tag.vault_id != vault.pk):
            return None, Response(status=status.HTTP_404_NOT_FOUND)

        payload = tag_metadata_payload(
            tag_uuid=data["uuid"],
            vault_uuid=vault.uuid,
            signer_account_uuid=identity.uuid,
            encrypted_name=data["encrypted_name"],
            color=data["color"],
        )
        try:
            verify_record(payload, identity.sig_public, data["metadata_sig"])
        except AttestationError:
            return None, _signature_refused()
        return vault, None


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class TagListView(_TagWriteMixin, CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        tags=["Vault"],
        summary="List the tags of one vault",
        parameters=[
            OpenApiParameter("vault", str, required=True, description="Vault UUID")
        ],
        responses=VaultTagSerializer(many=True),
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
        return Response(
            VaultTagSerializer(visible_tags(request.user, vault), many=True).data
        )

    @extend_schema(
        tags=["Vault"],
        summary="Create a tag",
        request=VaultTagWriteSerializer,
        responses={201: VaultTagSerializer},
    )
    @sensitive_variables()
    def post(self, request):
        serializer = VaultTagWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        vault, refusal = self._verified(request, data)
        if refusal is not None:
            return refusal

        try:
            with transaction.atomic():
                tag = VaultTag(
                    uuid=data["uuid"],
                    vault=vault,
                    encrypted_name=data["encrypted_name"],
                    color=data["color"],
                    metadata_sig=data["metadata_sig"],
                )
                tag.save(force_insert=True)
        except IntegrityError:
            # The UUID is the client's, so a retry that lost its answer lands
            # here rather than overwriting a row that already exists - which
            # may not even belong to this caller.
            return Response(status=status.HTTP_409_CONFLICT)

        return Response(VaultTagSerializer(tag).data, status=status.HTTP_201_CREATED)


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class TagDetailView(_TagWriteMixin, CacheControlMixin, APIView):
    cache_no_store = True

    def _reachable_tag(self, request, uuid, vault):
        return visible_tags(request.user, vault).filter(uuid=uuid).first()

    @extend_schema(
        tags=["Vault"],
        summary="Rename or recolour a tag",
        request=VaultTagWriteSerializer,
        responses={200: VaultTagSerializer},
    )
    @sensitive_variables()
    def patch(self, request, uuid):
        serializer = VaultTagWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["uuid"] != uuid:
            return Response(
                {"detail": "The body names another tag."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        vault = reachable_vault(request.user, data["vault"])
        if vault is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        tag = self._reachable_tag(request, uuid, vault)
        if tag is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        _, refusal = self._verified(request, data, tag=tag)
        if refusal is not None:
            return refusal

        tag.encrypted_name = data["encrypted_name"]
        tag.color = data["color"]
        tag.metadata_sig = data["metadata_sig"]
        tag.save(update_fields=["encrypted_name", "color", "metadata_sig"])
        return Response(VaultTagSerializer(tag).data)


@method_decorator(sensitive_post_parameters("metadata_sig"), name="dispatch")
class TagDeleteView(CacheControlMixin, APIView):
    """Deleting a tag re-signs every entry that carries it, in one go.

    tag_uuids is inside each carrier's signature, so removing the tag without
    the new signatures would leave rows signed over a tag set they no longer
    have. Either the whole removal lands or none of it does.
    """

    cache_no_store = True

    @extend_schema(
        tags=["Vault"],
        summary="Delete a tag, re-signing the entries that carry it",
        request=TagDeleteSerializer,
        responses={204: None},
    )
    @sensitive_variables()
    def post(self, request, uuid):
        identity = active_identity(request.user)
        tag = VaultTag.objects.filter(uuid=uuid).first()
        if identity is None or tag is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        vault = reachable_vault(request.user, tag.vault_id)
        if (
            vault is None
            or visible_tags(request.user, vault).filter(pk=tag.pk).first() is None
        ):
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = TagDeleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submitted = {
            item["uuid"]: item for item in serializer.validated_data["entries"]
        }

        try:
            with transaction.atomic():
                # Trashed carriers included: deleted_at is a view, and the
                # tag is still in their signature.
                carriers = list(
                    VaultEntry.objects.filter(tags=tag).prefetch_related(
                        "tags", "fields"
                    )
                )
                if {entry.uuid for entry in carriers} != set(submitted):
                    raise _ContentsChanged(
                        "The submitted entries do not match the tag's carriers."
                    )

                for entry in carriers:
                    item = submitted[entry.uuid]
                    payload = entry_signature_payload(
                        entry,
                        signer_account_uuid=identity.uuid,
                        tag_uuids=[t.uuid for t in entry.tags.all() if t.pk != tag.pk],
                        fields={
                            field.field_id: field.encrypted_value
                            for field in entry.fields.all()
                        },
                    )
                    try:
                        verify_record(
                            payload, identity.sig_public, item["metadata_sig"]
                        )
                    except AttestationError as exc:
                        raise _SignatureRefused from exc
                    written = VaultEntry.objects.filter(
                        pk=entry.pk, metadata_sig=entry.metadata_sig
                    ).update(metadata_sig=item["metadata_sig"])
                    if written != 1:
                        raise _ContentsChanged(
                            "An entry changed while the tag was being deleted."
                        )
                    entry.tags.remove(tag)
                tag.delete()
        except _SignatureRefused:
            return Response(
                {"detail": "An entry signature does not verify."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except _ContentsChanged as refusal:
            return Response({"detail": refusal.detail}, status=status.HTTP_409_CONFLICT)

        return Response(status=status.HTTP_204_NO_CONTENT)
