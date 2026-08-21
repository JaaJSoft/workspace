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

from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin

from .models import AccountIdentity
from .serializers import AccountEnvelopeSerializer
from .throttling import (
    AccountEnvelopeBurstThrottle,
    AccountEnvelopeIpThrottle,
    AccountEnvelopeUserThrottle,
    AccountInitIpThrottle,
    AccountInitUserThrottle,
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
    )
    def post(self, request):
        identity = AccountIdentity.objects.filter(user=request.user).first()
        if identity is None:
            identity = AccountIdentity.objects.create(
                user=request.user, kdf_salt=_new_salt()
            )
            code = status.HTTP_201_CREATED
        elif identity.state == AccountIdentity.State.ACTIVE:
            # Not the uniform 404 used elsewhere: that rule hides the existence
            # of other people's resources. Here the caller is asking about
            # their own account, and the refusal is the answer they need - a
            # fresh salt would leave their sealed keys underivable.
            return Response(status=status.HTTP_409_CONFLICT)
        else:
            code = status.HTTP_200_OK

        return Response(
            {"account_uuid": str(identity.uuid), "kdf_salt": identity.kdf_salt},
            status=code,
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
