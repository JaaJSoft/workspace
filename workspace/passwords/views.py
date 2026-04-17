import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import LoginEntry
from .serializers import (
    LoginEntryCreateSerializer,
    LoginEntrySerializer,
    LoginEntryUpdateSerializer,
    VaultSerializer,
    VaultSetupSerializer,
    VaultUnlockResponseSerializer,
    VaultUnlockSerializer,
)
from .services.vault import VaultService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vault endpoints
# ---------------------------------------------------------------------------

class VaultView(APIView):
    """GET /api/v1/passwords/vault — vault metadata and KDF parameters."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get vault metadata",
        description=(
            "Return the authenticated user's default vault. "
            "Creates the vault automatically on first access. "
            "KDF parameters are used by the client to reproduce the derived key."
        ),
        responses={200: VaultSerializer},
    )
    def get(self, request):
        vault = VaultService.get_or_create_vault(request.user)
        return Response(VaultSerializer(vault).data)


class VaultSetupView(APIView):
    """POST /api/v1/passwords/vault/setup — set or rotate master password."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Set up or rotate vault master password",
        description=(
            "Configure the vault's master password for the first time, or rotate it. "
            "The client must supply a client-derived hash of the stretched master key "
            "together with the vault key re-encrypted under the new master key."
        ),
        request=VaultSetupSerializer,
        responses={
            200: VaultSerializer,
            400: OpenApiResponse(description="Validation error"),
        },
    )
    def post(self, request):
        vault = VaultService.get_or_create_vault(request.user)
        serializer = VaultSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        vault = VaultService.setup_vault(
            vault=vault,
            client_master_hash=d['client_master_hash'],
            protected_vault_key=d['protected_vault_key'],
            kdf_salt=d['kdf_salt'],
            kdf_algorithm=d.get('kdf_algorithm'),
            kdf_iterations=d.get('kdf_iterations'),
            kdf_memory=d.get('kdf_memory'),
            kdf_parallelism=d.get('kdf_parallelism'),
        )
        return Response(VaultSerializer(vault).data)


class VaultUnlockView(APIView):
    """POST /api/v1/passwords/vault/unlock — verify master password hash."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Unlock vault",
        description=(
            "Verify the client-derived master password hash against the stored hash. "
            "On success, returns the protected vault key so the client can decrypt it "
            "with the stretched master key and gain access to entry ciphertext."
        ),
        request=VaultUnlockSerializer,
        responses={
            200: VaultUnlockResponseSerializer,
            400: OpenApiResponse(description="Vault not set up yet"),
            401: OpenApiResponse(description="Invalid master password"),
        },
    )
    def post(self, request):
        vault = VaultService.get_or_create_vault(request.user)
        if not vault.is_setup:
            return Response(
                {'detail': 'Vault has not been set up yet.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = VaultUnlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ok, protected_key = VaultService.verify_unlock(
            vault, serializer.validated_data['client_master_hash']
        )
        if not ok:
            return Response(
                {'detail': 'Invalid master password.'},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(VaultUnlockResponseSerializer({'protected_vault_key': protected_key}).data)


# ---------------------------------------------------------------------------
# LoginEntry endpoints
# ---------------------------------------------------------------------------

class LoginEntryListCreateView(APIView):
    """GET/POST /api/v1/passwords/entries"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List login entries",
        description="Return all non-deleted login entries owned by the authenticated user.",
        parameters=[
            OpenApiParameter(
                name='vault',
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description="Filter by vault UUID.",
            ),
            OpenApiParameter(
                name='trashed',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="When true, return only soft-deleted entries.",
            ),
            OpenApiParameter(
                name='favorites',
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                required=False,
                description="When true, return only favorited entries.",
            ),
        ],
        responses={200: LoginEntrySerializer(many=True)},
    )
    def get(self, request):
        vault_uuid = request.query_params.get('vault')
        trashed = request.query_params.get('trashed', '').lower() == 'true'
        favorites = request.query_params.get('favorites', '').lower() == 'true'

        qs = VaultService.get_login_entries(
            request.user,
            vault_uuid=vault_uuid,
            include_deleted=trashed,
        )
        if trashed:
            qs = qs.filter(deleted_at__isnull=False)
        if favorites:
            qs = qs.filter(is_favorite=True)

        serializer = LoginEntrySerializer(qs, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Create a login entry",
        description=(
            "Create a new login entry. All sensitive fields (username, password, TOTP secret, "
            "and the entry name) must be AES-256-GCM encrypted client-side before submission."
        ),
        request=LoginEntryCreateSerializer,
        responses={
            201: LoginEntrySerializer,
            400: OpenApiResponse(description="Validation error"),
        },
    )
    def post(self, request):
        serializer = LoginEntryCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        entry = serializer.save()
        return Response(LoginEntrySerializer(entry).data, status=status.HTTP_201_CREATED)


class LoginEntryDetailView(APIView):
    """GET/PUT/DELETE /api/v1/passwords/entries/<uuid>"""

    permission_classes = [IsAuthenticated]

    def _get_entry(self, request, uuid):
        return (
            VaultService.get_login_entries(request.user, include_deleted=True)
            .filter(uuid=uuid)
            .first()
        )

    @extend_schema(
        summary="Retrieve a login entry",
        responses={
            200: LoginEntrySerializer,
            404: OpenApiResponse(description="Entry not found"),
        },
    )
    def get(self, request, uuid):
        entry = self._get_entry(request, uuid)
        if entry is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(LoginEntrySerializer(entry).data)

    @extend_schema(
        summary="Update a login entry",
        request=LoginEntryUpdateSerializer,
        responses={
            200: LoginEntrySerializer,
            400: OpenApiResponse(description="Validation error"),
            404: OpenApiResponse(description="Entry not found"),
        },
    )
    def put(self, request, uuid):
        entry = self._get_entry(request, uuid)
        if entry is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = LoginEntryUpdateSerializer(entry, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(LoginEntrySerializer(entry).data)

    @extend_schema(
        summary="Delete a login entry",
        description=(
            "Soft-delete the entry by setting deleted_at. "
            "Permanently deleted entries (already in trash) are hard-deleted."
        ),
        responses={
            204: OpenApiResponse(description="Deleted"),
            404: OpenApiResponse(description="Entry not found"),
        },
    )
    def delete(self, request, uuid):
        entry = self._get_entry(request, uuid)
        if entry is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if entry.deleted_at is None:
            # First delete → move to trash
            entry.deleted_at = timezone.now()
            entry.save(update_fields=['deleted_at'])
        else:
            # Already in trash → hard delete
            entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
