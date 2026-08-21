"""The account envelope API.

Four endpoints over one row: the caller's :class:`AccountIdentity`. The server
generates the KDF salt - the only random material it owns, and it is public -
then stores opaque text it can never open.

The rule every view here obeys: the identity row is created once and updated
in place, forever. Its sealed private keys are the only path back to every
VaultKeyWrap the account holds, so deleting the row, recreating it, or
regenerating its salt destroys every vault the user has. Nothing reports it;
the failure surfaces the next time they try to unlock.
"""

import base64
import os

from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import (
    sensitive_post_parameters,
    sensitive_variables,
)
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin

from .models import AccountIdentity
from .serializers import (
    AccountEnvelopeSerializer,
    AccountFinalizeSerializer,
    AccountInitResponseSerializer,
    AccountRotateSerializer,
)
from .services.attestation import AttestationError, verify_kex_pub_attestation
from .throttling import (
    AccountEnvelopeBurstThrottle,
    AccountEnvelopeIpThrottle,
    AccountEnvelopeUserThrottle,
    AccountFinalizeIpThrottle,
    AccountInitIpThrottle,
    AccountInitUserThrottle,
    AccountRotateUserThrottle,
)

SALT_LENGTH = 32

# Every field an account envelope request can carry, so a traceback cannot
# render one from a frame this module never wrote.
SENSITIVE_BODY_FIELDS = (
    "kdf_params",
    "kex_public",
    "sig_public",
    "wrapped_kex_priv",
    "wrapped_sig_priv",
    "sig_over_kex_pub",
)


def _new_salt() -> str:
    return base64.urlsafe_b64encode(os.urandom(SALT_LENGTH)).decode("ascii").rstrip("=")


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class AccountInitView(APIView):
    throttle_classes = [AccountInitIpThrottle, AccountInitUserThrottle]

    @extend_schema(
        tags=["Vault"],
        summary="Start the account identity",
        description=(
            "Creates the pending identity and returns the account UUID and the "
            "KDF salt the browser needs to derive its account master key. "
            "Idempotent while the identity is pending."
        ),
        request=None,
        responses={
            200: AccountInitResponseSerializer,
            201: AccountInitResponseSerializer,
        },
    )
    def post(self, request):
        # get_or_create rather than a lookup and an insert: `user` is unique,
        # so two first calls racing each other would make the loser raise
        # IntegrityError - a 500 earned by double-clicking a button.
        identity, created = AccountIdentity.objects.get_or_create(
            user=request.user, defaults={"kdf_salt": _new_salt()}
        )
        if not created and identity.state == AccountIdentity.State.ACTIVE:
            # Not the design's uniform 404, which hides the existence of other
            # people's resources. The caller is asking about their own account,
            # and the refusal is the answer they need - a fresh salt would
            # leave their sealed keys underivable.
            return Response(status=status.HTTP_409_CONFLICT)

        return Response(
            {"account_uuid": str(identity.uuid), "kdf_salt": identity.kdf_salt},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class AccountEnvelopeView(CacheControlMixin, APIView):
    cache_no_store = True
    throttle_classes = [
        AccountEnvelopeBurstThrottle,
        AccountEnvelopeUserThrottle,
        AccountEnvelopeIpThrottle,
    ]

    @extend_schema(
        tags=["Vault"],
        summary="Fetch the account envelope",
        responses=AccountEnvelopeSerializer,
    )
    def get(self, request):
        identity = AccountIdentity.objects.filter(user=request.user).first()
        if identity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(AccountEnvelopeSerializer(identity).data)


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class AccountFinalizeView(APIView):
    throttle_classes = [AccountFinalizeIpThrottle]

    @extend_schema(
        tags=["Vault"],
        summary="Finalize the account identity",
        description=(
            "Stores the account public keys, the sealed private keys and the "
            "attestation over the key exchange public key, and activates the "
            "identity. Refused once the identity is active."
        ),
        request=AccountFinalizeSerializer,
        responses={201: None},
    )
    @sensitive_variables()
    def post(self, request):
        identity = AccountIdentity.objects.filter(user=request.user).first()
        if identity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if identity.state == AccountIdentity.State.ACTIVE:
            return Response(status=status.HTTP_409_CONFLICT)

        serializer = AccountFinalizeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The server opens nothing, but it refuses to store an identity whose
        # public key nobody vouched for: every other client would reject what
        # this account signs, and only at unlock time, far from here.
        try:
            verify_kex_pub_attestation(
                identity.uuid,
                data["kex_public"],
                data["sig_public"],
                data["sig_over_kex_pub"],
            )
        except AttestationError:
            return Response(
                {"detail": "The account attestation does not verify."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # A conditional update, not a check followed by a save: the state read
        # above and the write are otherwise two steps, and a second finalize
        # slipping between them would replace the sealed private keys while
        # every key wrap already made still points at the pair they replaced.
        activated = AccountIdentity.objects.filter(
            pk=identity.pk, state=AccountIdentity.State.PENDING
        ).update(state=AccountIdentity.State.ACTIVE, updated_at=timezone.now(), **data)
        if not activated:
            return Response(status=status.HTTP_409_CONFLICT)
        return Response(status=status.HTTP_201_CREATED)


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class AccountRotateView(CacheControlMixin, APIView):
    cache_no_store = True
    throttle_classes = [AccountRotateUserThrottle]

    @extend_schema(
        tags=["Vault"],
        summary="Rotate the vault password envelope",
        description=(
            "Re-wraps the same account private keys under a key derived from a "
            "new vault password. No vault is re-encrypted: the vault keys have "
            "not changed, only the envelope that seals the account keys."
        ),
        request=AccountRotateSerializer,
        responses={200: None},
    )
    @sensitive_variables()
    def post(self, request):
        identity = AccountIdentity.objects.filter(
            user=request.user, state=AccountIdentity.State.ACTIVE
        ).first()
        if identity is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = AccountRotateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # update_fields names the three columns a rotation rewrites, so no
        # later edit here can widen it into a re-identification: the sealed
        # private keys are the only path back to every VaultKeyWrap, and a
        # recreated identity orphans all of them without a word.
        for field, value in serializer.validated_data.items():
            setattr(identity, field, value)
        identity.save(
            update_fields=[
                "kdf_params",
                "wrapped_kex_priv",
                "wrapped_sig_priv",
                "updated_at",
            ]
        )
        return Response(status=status.HTTP_200_OK)
