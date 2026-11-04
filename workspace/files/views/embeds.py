"""Resolve the files a note embeds, for the person reading the note."""

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import (
    BatchTooLarge,
    MalformedUuid,
    UuidBatchError,
    parse_uuid_batch,
)

from ..services.embeds import MAX_EMBEDS, resolve_embeds


def _refused(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=["Files"])
class FileEmbedsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Resolve file embeds",
        description=(
            "Tell the markdown editor how to draw the files a note embeds, as "
            "the caller sees them. Every submitted UUID comes back as a key, "
            "spelled in its canonical form, with a `status`: `ok` (the file "
            "and the URLs to show it), `trashed` or `quarantined` (only its "
            "name), or `unavailable` - the file does not exist or the caller "
            "may not open it, which the answer does not tell apart."
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "uuids": {
                        "type": "array",
                        "items": {"type": "string", "format": "uuid"},
                        "maxItems": MAX_EMBEDS,
                    },
                },
                "required": ["uuids"],
            }
        },
        responses={
            200: OpenApiResponse(response=OpenApiTypes.OBJECT),
            400: OpenApiResponse(description="Invalid request."),
        },
    )
    def post(self, request):
        try:
            uuids = parse_uuid_batch(request.data, key="uuids", max_items=MAX_EMBEDS)
        except BatchTooLarge:
            return _refused(f"Too many files (max {MAX_EMBEDS}).")
        except MalformedUuid:
            return _refused("Malformed UUID in uuids.")
        except UuidBatchError:
            return _refused("uuids must be a non-empty list.")
        answers = resolve_embeds(request.user, uuids)
        return Response({str(uuid): answer for uuid, answer in answers.items()})
