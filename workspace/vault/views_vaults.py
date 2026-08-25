"""The vault collection and its members.

Two rules run through every view here. Refusals are uniform - a vault that
does not exist and a vault the caller may not touch both answer 404, so no
status code and no response time says whether someone else owns one. And
nothing is stored unsigned: the server rebuilds the metadata it is about to
write, re-encodes it canonically and checks the caller's signature over it,
which is the only part of the trust chain it can hold.
"""

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin

from .models import Vault, VaultKeyWrap
from .queries import active_identity, user_vault_ids
from .serializers import VaultCreateSerializer, VaultSerializer
from .services.attestation import AttestationError
from .services.metadata import vault_metadata_payload, verify_vault_metadata

SENSITIVE_BODY_FIELDS = (
    "encrypted_name",
    "encrypted_description",
    "wrapped_key",
    "metadata_sig",
)


def _signature_refused():
    return Response(
        {"detail": "The vault metadata signature does not verify."},
        status=status.HTTP_400_BAD_REQUEST,
    )


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class VaultListView(CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        tags=["Vault"],
        summary="List the vaults the caller can open",
        responses=VaultSerializer(many=True),
    )
    @sensitive_variables()
    def get(self, request):
        vaults = (
            Vault.objects.filter(uuid__in=user_vault_ids(request.user))
            .select_related("owner__vault_identity")
            .prefetch_related(
                Prefetch(
                    "key_wraps",
                    queryset=VaultKeyWrap.objects.filter(recipient=request.user),
                    to_attr="own_wraps",
                )
            )
        )
        return Response(
            VaultSerializer(vaults, many=True, context={"request": request}).data
        )

    @extend_schema(
        tags=["Vault"],
        summary="Create a vault",
        request=VaultCreateSerializer,
        responses={201: VaultSerializer},
    )
    @sensitive_variables()
    def post(self, request):
        identity = active_identity(request.user)
        if identity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = VaultCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # key_version and is_favorite are the server's to set at creation, and
        # they are inside the signature: a client that signed anything else
        # fails here rather than writing a vault it cannot re-verify.
        payload = vault_metadata_payload(
            vault_uuid=data["uuid"],
            owner_account_uuid=identity.uuid,
            encrypted_name=data["encrypted_name"],
            encrypted_description=data["encrypted_description"],
            icon=data["icon"],
            color=data["color"],
            key_version=1,
            is_favorite=False,
        )
        try:
            verify_vault_metadata(payload, identity.sig_public, data["metadata_sig"])
        except AttestationError:
            return _signature_refused()

        try:
            with transaction.atomic():
                vault = Vault.objects.create(
                    uuid=data["uuid"],
                    owner=request.user,
                    encrypted_name=data["encrypted_name"],
                    encrypted_description=data["encrypted_description"],
                    icon=data["icon"],
                    color=data["color"],
                    metadata_sig=data["metadata_sig"],
                )
                VaultKeyWrap.objects.create(
                    vault=vault,
                    recipient=request.user,
                    wrapped_key=data["wrapped_key"],
                    key_version=vault.key_version,
                    hpke_suite=data["hpke_suite"],
                )
        except IntegrityError:
            # The UUID is the client's, so a retry that lost its answer lands
            # here rather than creating a second vault under a second key.
            return Response(status=status.HTTP_409_CONFLICT)

        vault.own_wraps = list(vault.key_wraps.all())
        return Response(
            VaultSerializer(vault, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )
