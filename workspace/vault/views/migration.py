"""Moving stored rows from a superseded algorithm to the current suite.

The server holds no key, so it can never re-seal anything: it lists what a
caller still has under a superseded id, and checks the rewrites the browser
sends back. The listing is a shortcut, never a guarantee - a server that omits
rows only leaves them where they are, which refusing the writes would do too.
"""

from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin

from ..queries import active_identity, reachable_vault
from ..serializers import MigrateBatchSerializer
from ..services.census import stale_rows
from ..services.migration import Conflict, Refused, RowOutOfReach, apply_batch
from ..throttling import MigrateUserThrottle

SENSITIVE_BODY_FIELDS = (
    "encrypted_name",
    "encrypted_description",
    "encrypted_notes",
    "fields",
    "metadata_sig",
    "expected_sig",
    "wrapped_key",
    "wrapped_key_expected",
)


class MigrationListView(CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        tags=["Vault"], summary="List the caller's rows under a superseded algorithm"
    )
    def get(self, request):
        if active_identity(request.user) is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response({"vaults": stale_rows(request.user)})


@method_decorator(sensitive_post_parameters(*SENSITIVE_BODY_FIELDS), name="dispatch")
class VaultMigrateView(CacheControlMixin, APIView):
    cache_no_store = True
    throttle_classes = [MigrateUserThrottle]

    @extend_schema(
        tags=["Vault"],
        summary="Rewrite rows under the current suite, all or nothing",
        request=MigrateBatchSerializer,
        responses={204: None},
    )
    @sensitive_variables()
    def post(self, request, uuid):
        identity = active_identity(request.user)
        vault = reachable_vault(request.user, uuid)
        if identity is None or vault is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = MigrateBatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            apply_batch(
                request.user, identity, vault, serializer.validated_data["items"]
            )
        except RowOutOfReach:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except Conflict as conflict:
            return Response(
                {
                    "detail": "A row changed since it was read.",
                    "kind": conflict.kind,
                    "uuid": str(conflict.uuid) if conflict.uuid else None,
                },
                status=status.HTTP_409_CONFLICT,
            )
        except Refused as refusal:
            return Response(
                {"detail": refusal.detail}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
