"""Folders, the only structure a vault has.

A folder's parent and position are plaintext, so the signature is the only
thing that covers them: the server rebuilds the payload from the columns it is
about to write and refuses anything else. Deleting one is not here - it moves
entries and lives in its own transactional endpoint.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import parse_uuid_or_none

from .models import VaultFolder
from .queries import active_identity, reachable_vault, visible_folders
from .serializers import VaultFolderSerializer, VaultFolderWriteSerializer
from .services.attestation import AttestationError
from .services.metadata import folder_metadata_payload, verify_record

SENSITIVE_BODY_FIELDS = ("encrypted_name", "metadata_sig")


def _signature_refused():
    return Response(
        {"detail": "The folder metadata signature does not verify."},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _bad_parent():
    return Response(
        {"detail": "The parent folder does not exist in this vault."},
        status=status.HTTP_400_BAD_REQUEST,
    )


class _FolderWriteMixin:
    def _verified(self, request, data, *, folder=None):
        """``((vault, parent), None)`` or ``(None, Response)`` to return as-is."""
        identity = active_identity(request.user)
        if identity is None:
            return None, Response(status=status.HTTP_404_NOT_FOUND)
        vault = reachable_vault(request.user, data["vault"])
        if vault is None or (folder is not None and folder.vault_id != vault.pk):
            return None, Response(status=status.HTTP_404_NOT_FOUND)

        parent = None
        if data["parent"] is not None:
            # visible_folders scopes to the vault, so a parent from another
            # vault and one that does not exist both come back empty - and
            # both are the client's error, not a missing resource.
            parent = (
                visible_folders(request.user, vault).filter(uuid=data["parent"]).first()
            )
            if parent is None:
                return None, _bad_parent()

        payload = folder_metadata_payload(
            folder_uuid=data["uuid"],
            vault_uuid=vault.uuid,
            signer_account_uuid=identity.uuid,
            parent_uuid=parent.uuid if parent else None,
            position=data["position"],
            encrypted_name=data["encrypted_name"],
        )
        try:
            verify_record(payload, identity.sig_public, data["metadata_sig"])
        except AttestationError:
            return None, _signature_refused()
        return (vault, parent), None


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class FolderListView(_FolderWriteMixin, CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        tags=["Vault"],
        summary="List the folders of one vault",
        parameters=[
            OpenApiParameter("vault", str, required=True, description="Vault UUID")
        ],
        responses=VaultFolderSerializer(many=True),
    )
    @sensitive_variables()
    def get(self, request):
        # A collection filter, so a malformed value is the client's bug and
        # answers 400; an unreachable vault answers 404, like everything else
        # the caller may not touch.
        vault_uuid = parse_uuid_or_none(request.query_params.get("vault"))
        if vault_uuid is None:
            return Response(
                {"detail": "A well-formed vault UUID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        vault = reachable_vault(request.user, vault_uuid)
        if vault is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        folders = visible_folders(request.user, vault)
        return Response(VaultFolderSerializer(folders, many=True).data)

    @extend_schema(
        tags=["Vault"],
        summary="Create a folder",
        request=VaultFolderWriteSerializer,
        responses={201: VaultFolderSerializer},
    )
    @sensitive_variables()
    def post(self, request):
        serializer = VaultFolderWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        verified, refusal = self._verified(request, data)
        if refusal is not None:
            return refusal
        vault, parent = verified

        folder = VaultFolder(
            uuid=data["uuid"],
            vault=vault,
            parent=parent,
            encrypted_name=data["encrypted_name"],
            position=data["position"],
            metadata_sig=data["metadata_sig"],
        )
        try:
            with transaction.atomic():
                folder.full_clean(exclude=["uuid"])
                folder.save(force_insert=True)
        except ValidationError:
            return _bad_parent()
        except IntegrityError:
            # The UUID is the client's, so a retry that lost its answer lands
            # here rather than overwriting a row that already exists - which
            # may not even belong to this caller.
            return Response(status=status.HTTP_409_CONFLICT)

        return Response(
            VaultFolderSerializer(folder).data, status=status.HTTP_201_CREATED
        )


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class FolderDetailView(_FolderWriteMixin, CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        tags=["Vault"],
        summary="Rename or move a folder",
        request=VaultFolderWriteSerializer,
        responses={200: VaultFolderSerializer},
    )
    @sensitive_variables()
    def patch(self, request, uuid):
        serializer = VaultFolderWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data["uuid"] != uuid:
            return Response(
                {"detail": "The body names another folder."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        identity = active_identity(request.user)
        vault = reachable_vault(request.user, data["vault"])
        if identity is None or vault is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        folder = visible_folders(request.user, vault).filter(uuid=uuid).first()
        if folder is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        verified, refusal = self._verified(request, data, folder=folder)
        if refusal is not None:
            return refusal
        _, parent = verified

        folder.parent = parent
        folder.encrypted_name = data["encrypted_name"]
        folder.position = data["position"]
        folder.metadata_sig = data["metadata_sig"]
        try:
            with transaction.atomic():
                folder.full_clean(exclude=["uuid"])
                folder.save(
                    update_fields=[
                        "parent",
                        "encrypted_name",
                        "position",
                        "metadata_sig",
                        "updated_at",
                    ]
                )
        except ValidationError:
            return _bad_parent()

        return Response(VaultFolderSerializer(folder).data)
