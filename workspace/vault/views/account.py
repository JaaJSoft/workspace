"""The account envelope API.

Five endpoints over one row: the caller's :class:`AccountIdentity`. The server
generates the KDF salt - the only random material it owns, and it is public -
then stores opaque text it can never open.

The rule every view here obeys: the identity row is created once and updated
in place, forever. Its sealed private keys are the only path back to every
VaultKeyWrap the account holds, so deleting the row, recreating it, or
regenerating its salt destroys every vault the user has. Nothing reports it;
the failure surfaces the next time they try to unlock.
"""

import secrets

from django.db.models import Case, F, Q, When
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

from ..models import AccountIdentity
from ..serializers import (
    AccountConfirmSerializer,
    AccountEnvelopeSerializer,
    AccountFinalizeSerializer,
    AccountInitResponseSerializer,
    AccountRotateSerializer,
)
from ..services.attestation import AttestationError, verify_kex_pub_attestation
from ..throttling import (
    AccountConfirmUserThrottle,
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
    "expected_wrapped_kex_priv",
)


def _new_salt() -> str:
    return secrets.token_urlsafe(SALT_LENGTH)


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
    @sensitive_variables()
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
            "Re-wraps the same account private keys under the current suite, "
            "against the envelope the client read. The envelope it replaces is "
            "kept until an unlock proves the new one."
        ),
        request=AccountRotateSerializer,
        responses={200: None, 404: None, 409: None},
    )
    @sensitive_variables()
    def post(self, request):
        mine = AccountIdentity.objects.filter(
            user=request.user, state=AccountIdentity.State.ACTIVE
        )
        if not mine.exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = AccountRotateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # One UPDATE, conditioned on the envelope the client read. SQL
        # evaluates every right-hand side against the row as it was before the
        # statement (PostgreSQL and SQLite alike), so the current envelope is
        # copied into the previous slot only when that slot was empty - the
        # current envelope had been proven - and an unproven one never
        # displaces the last envelope an unlock opened.
        previous_empty = Q(previous_wrapped_kex_priv__isnull=True)

        def kept_or_taken(column):
            return Case(
                When(previous_empty, then=F(column)),
                default=F(f"previous_{column}"),
                output_field=AccountIdentity._meta.get_field(f"previous_{column}"),
            )

        rotated = mine.filter(
            wrapped_kex_priv=data["expected_wrapped_kex_priv"],
        ).update(
            previous_kdf_algo=kept_or_taken("kdf_algo"),
            previous_kdf_params=kept_or_taken("kdf_params"),
            previous_wrapped_kex_priv=kept_or_taken("wrapped_kex_priv"),
            previous_wrapped_sig_priv=kept_or_taken("wrapped_sig_priv"),
            kdf_algo=data["kdf_algo"],
            kdf_params=data["kdf_params"],
            wrapped_kex_priv=data["wrapped_kex_priv"],
            wrapped_sig_priv=data["wrapped_sig_priv"],
            updated_at=timezone.now(),
        )
        return Response(
            status=status.HTTP_200_OK if rotated else status.HTTP_409_CONFLICT
        )


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class AccountConfirmView(CacheControlMixin, APIView):
    cache_no_store = True
    throttle_classes = [AccountConfirmUserThrottle]

    @extend_schema(
        tags=["Vault"],
        summary="Confirm the current account envelope",
        description=(
            "Called after an unlock opened the current envelope. Drops the "
            "previous envelope, but only while the current one is still the "
            "envelope that was opened."
        ),
        request=AccountConfirmSerializer,
        responses={204: None},
    )
    @sensitive_variables()
    def post(self, request):
        serializer = AccountConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mine = AccountIdentity.objects.filter(
            user=request.user, state=AccountIdentity.State.ACTIVE
        )
        if not mine.exists():
            return Response(status=status.HTTP_404_NOT_FOUND)
        mine.filter(
            wrapped_kex_priv=serializer.validated_data["wrapped_kex_priv"]
        ).update(
            previous_kdf_algo=None,
            previous_kdf_params=None,
            previous_wrapped_kex_priv=None,
            previous_wrapped_sig_priv=None,
            updated_at=timezone.now(),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
