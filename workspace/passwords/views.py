import logging

from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import LoginEntry, PasswordFolder
from .serializers import (
    FolderCreateSerializer,
    FolderSerializer,
    FolderUpdateSerializer,
    LoginEntryCreateSerializer,
    LoginEntrySerializer,
    LoginEntryUpdateSerializer,
    VaultCreateSerializer,
    VaultSerializer,
    VaultSetupResponseSerializer,
    VaultSetupSerializer,
    VaultUnlockResponseSerializer,
    VaultUnlockSerializer,
)
from .services.folders import FolderService
from .services.vault import VaultService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vault endpoints
# ---------------------------------------------------------------------------

class VaultListCreateView(APIView):
    """GET /api/v1/passwords/vaults — list vaults
    POST /api/v1/passwords/vaults — create vault stub
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Passwords - Vaults'],
        summary="List vaults",
        description="Return all vaults the authenticated user owns or has accepted membership in.",
        responses={200: VaultSerializer(many=True)},
    )
    def get(self, request):
        vaults = VaultService.list_vaults(request.user)
        return Response(VaultSerializer(vaults, many=True).data)

    @extend_schema(
        tags=['Passwords - Vaults'],
        summary="Create a vault",
        description=(
            "Create a new named vault. The server pre-generates a composite KDF salt "
            "(user_uuid_bytes || random) returned in the response. "
            "The client then performs key derivation client-side and calls "
            "POST /api/v1/passwords/vaults/<uuid>/setup with the cryptographic material. "
            "The setup response returns the protected_vault_key so the vault is immediately "
            "unlocked — no separate /unlock call needed after creation."
        ),
        request=VaultCreateSerializer,
        responses={
            201: VaultSerializer,
            400: OpenApiResponse(description="Validation error"),
        },
    )
    def post(self, request):
        serializer = VaultCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        vault = VaultService.create_vault(
            user=request.user,
            name=d.get('name', 'Personal'),
            description=d.get('description', ''),
            icon=d.get('icon', 'vault'),
            color=d.get('color', 'primary'),
        )
        return Response(VaultSerializer(vault).data, status=status.HTTP_201_CREATED)


class VaultDetailView(APIView):
    """GET    /api/v1/passwords/vaults/<uuid> — retrieve a specific vault.
    DELETE /api/v1/passwords/vaults/<uuid> — permanently delete a vault (owner only).
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Passwords - Vaults'],
        summary="Retrieve a vault",
        responses={
            200: VaultSerializer,
            404: OpenApiResponse(description="Vault not found"),
        },
    )
    def get(self, request, uuid):
        vault = VaultService.get_vault(request.user, uuid)
        if vault is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(VaultSerializer(vault).data)

    @extend_schema(
        tags=['Passwords - Vaults'],
        summary="Delete a vault",
        description=(
            "Permanently delete a vault and all its contents (entries, folders, tags, "
            "shared access records). Only the vault owner can perform this action. "
            "Members with any role, including manager, receive 404."
        ),
        responses={
            204: OpenApiResponse(description="Deleted"),
            404: OpenApiResponse(description="Vault not found or not owner"),
        },
    )
    def delete(self, request, uuid):
        deleted = VaultService.delete_vault(request.user, uuid)
        if not deleted:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class VaultSetupView(APIView):
    """POST /api/v1/passwords/vaults/<uuid>/setup — initial setup or master-password rotation.

    Workflow
    ────────
    Creation flow (2 calls, no separate unlock):
      1. POST /vaults/           → vault stub + kdf_salt
      2. POST /vaults/<id>/setup → vault + protected_vault_key  ← client is now unlocked

    Rotation flow:
      POST /vaults/<id>/setup with a new kdf_salt to replace the existing salt.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Passwords - Vaults'],
        summary="Set up or rotate vault master password",
        description=(
            "Initial setup: omit kdf_salt — the server reuses the composite salt generated "
            "at vault creation. The response includes protected_vault_key so the client is "
            "immediately unlocked after setup.\n\n"
            "Rotation: supply a new kdf_salt to replace the existing one along with the "
            "re-encrypted protected_vault_key."
        ),
        request=VaultSetupSerializer,
        responses={
            200: VaultSetupResponseSerializer,
            400: OpenApiResponse(description="Validation error"),
            404: OpenApiResponse(description="Vault not found"),
        },
    )
    def post(self, request, uuid):
        vault = VaultService.get_vault(request.user, uuid)
        if vault is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        # Only the vault owner manages the master password
        if vault.user != request.user:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = VaultSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        vault = VaultService.setup_vault(
            vault=vault,
            client_master_hash=d['client_master_hash'],
            protected_vault_key=d['protected_vault_key'],
            kdf_salt=d.get('kdf_salt') or None,
            kdf_algorithm=d.get('kdf_algorithm'),
            kdf_iterations=d.get('kdf_iterations'),
            kdf_memory=d.get('kdf_memory'),
            kdf_parallelism=d.get('kdf_parallelism'),
        )
        return Response(VaultSetupResponseSerializer(vault).data)


class VaultUnlockView(APIView):
    """POST /api/v1/passwords/vaults/<uuid>/unlock — verify master password hash (owner only).

    Used for subsequent unlocks after the initial setup (e.g. user reopens the app).
    Shared-vault members decrypt their VaultMember.protected_vault_key client-side
    using their own private key — they do not use this endpoint.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Passwords - Vaults'],
        summary="Unlock vault",
        description=(
            "Verify the client-derived master password hash against the stored hash. "
            "On success, returns the protected vault key so the client can decrypt it "
            "with the stretched master key and gain access to entry ciphertext. "
            "Only the vault owner can unlock via this endpoint."
        ),
        request=VaultUnlockSerializer,
        responses={
            200: VaultUnlockResponseSerializer,
            400: OpenApiResponse(description="Vault not set up yet"),
            401: OpenApiResponse(description="Invalid master password"),
            404: OpenApiResponse(description="Vault not found"),
        },
    )
    def post(self, request, uuid):
        vault = VaultService.get_vault(request.user, uuid)
        if vault is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        if vault.user != request.user:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
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
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(VaultUnlockResponseSerializer({'protected_vault_key': protected_key}).data)


# ---------------------------------------------------------------------------
# Folder endpoints
# ---------------------------------------------------------------------------

class FolderListCreateView(APIView):
    """GET/POST /api/v1/passwords/vaults/<uuid>/folders"""

    permission_classes = [IsAuthenticated]

    def get(self, request, uuid):
        vault = VaultService.get_vault(request.user, uuid)
        if vault is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        folders = FolderService.list_folders(vault)
        return Response(FolderSerializer(folders, many=True).data)

    def post(self, request, uuid):
        vault = VaultService.get_vault(request.user, uuid)
        if vault is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = FolderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data
        try:
            folder = FolderService.create_folder(
                vault, name=d['name'],
                parent_uuid=str(d['parent']) if d.get('parent') else None,
            )
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(FolderSerializer(folder).data, status=status.HTTP_201_CREATED)


class FolderDetailView(APIView):
    """PUT/DELETE /api/v1/passwords/folders/<uuid>"""

    permission_classes = [IsAuthenticated]

    def _get_vault_for_folder(self, user, folder_uuid):
        folder = PasswordFolder.objects.filter(uuid=folder_uuid).select_related('vault').first()
        if folder is None:
            return None, None
        vault = VaultService.get_vault(user, folder.vault_id)
        if vault is None:
            return None, None
        return vault, folder

    def put(self, request, uuid):
        vault, folder = self._get_vault_for_folder(request.user, uuid)
        if vault is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        serializer = FolderUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = FolderService.update_folder(vault, str(uuid), name=serializer.validated_data['name'])
        return Response(FolderSerializer(updated).data)

    def delete(self, request, uuid):
        vault, folder = self._get_vault_for_folder(request.user, uuid)
        if vault is None:
            return Response({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        FolderService.delete_folder(vault, str(uuid))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# LoginEntry endpoints
# ---------------------------------------------------------------------------

class LoginEntryListCreateView(APIView):
    """GET/POST /api/v1/passwords/entries"""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['Passwords - Entries'],
        summary="List login entries",
        description="Return all non-deleted login entries the authenticated user can access.",
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

        return Response(LoginEntrySerializer(qs, many=True).data)

    @extend_schema(
        tags=['Passwords - Entries'],
        summary="Create a login entry",
        description=(
            "Create a new login entry. All sensitive fields (username, password, TOTP secret, "
            "and the entry name) must be AES-256-GCM encrypted client-side before submission. "
            "The vault must be owned by the user or the user must have editor/manager access."
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
        tags=['Passwords - Entries'],
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
        tags=['Passwords - Entries'],
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
        tags=['Passwords - Entries'],
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
            entry.deleted_at = timezone.now()
            entry.save(update_fields=['deleted_at'])
        else:
            entry.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
