"""Tags, the flat half of a vault's structure.

A tag's colour is plaintext, so metadata_sig is the only thing covering it -
the server rebuilds the payload from the columns it is about to write and
refuses anything else.

Unlike a folder, a tag can be deleted outright: nothing points at it under
RESTRICT, and the M2M rows go with it. The entries that lose it are then
carrying a signature over a tag set they no longer have, which is theirs to
repair by re-signing. The server never rewrites metadata_sig on their behalf -
that would be a server forging a client's signature.
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

from ..models import VaultTag
from ..queries import active_identity, reachable_vault, visible_tags
from ..serializers import VaultTagSerializer, VaultTagWriteSerializer
from ..services.attestation import AttestationError
from ..services.metadata import tag_metadata_payload, verify_record

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

    @extend_schema(tags=["Vault"], summary="Delete a tag", responses={204: None})
    def delete(self, request, uuid):
        tag = VaultTag.objects.filter(uuid=uuid).first()
        if tag is None or reachable_vault(request.user, tag.vault_id) is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        tag.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
