"""Moving stored rows from a superseded algorithm to the current suite.

The server holds no key, so it can never re-seal anything: it lists what a
caller still has under a superseded id, and checks the rewrites the browser
sends back. The listing is a shortcut, never a guarantee - a server that omits
rows only leaves them where they are, which refusing the writes would do too.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin

from ..queries import active_identity
from ..services.census import stale_rows


class MigrationListView(CacheControlMixin, APIView):
    cache_no_store = True

    @extend_schema(
        tags=["Vault"], summary="List the caller's rows under a superseded algorithm"
    )
    def get(self, request):
        if active_identity(request.user) is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response({"vaults": stale_rows(request.user)})
