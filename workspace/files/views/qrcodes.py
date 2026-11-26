"""A public link as a QR code, for its owner to show on a screen or print."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.qr import make_qr, qr_response

from ..queries import owned_share_links
from ..serializers import ShareLinkQRCodeQuerySerializer
from ..services.sharing import share_link_url


@extend_schema(tags=["Files"])
class ShareLinkQRCodeView(CacheControlMixin, APIView):
    # Whoever holds the image holds the link: nothing of it stays in a
    # browser cache for the next account on a shared machine to find.
    cache_no_store = True

    @extend_schema(
        summary="Render a public link as a QR code",
        description=(
            "The code holds the address the link opens at and nothing else: a "
            "password-protected link still asks whoever scans it for its "
            "password. Only the owner of the file or folder gets it; a link "
            "of someone else's answers 404, like one that does not exist."
        ),
        parameters=[ShareLinkQRCodeQuerySerializer],
        responses={
            (200, "image/svg+xml"): OpenApiTypes.BINARY,
            (200, "image/png"): OpenApiTypes.BINARY,
        },
    )
    def get(self, request, uuid):
        query = ShareLinkQRCodeQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        link = (
            owned_share_links(request.user)
            .filter(uuid=uuid)
            .select_related("file")
            .first()
        )
        if link is None:
            raise NotFound
        return qr_response(
            make_qr(share_link_url(request, link)),
            query.validated_data["kind"],
            f"{link.file.name}-qr",
        )
