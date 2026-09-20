from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import (
    BatchTooLarge,
    MalformedUuid,
    UuidBatchError,
    parse_uuid_batch,
)

from ..actions import actions_for
from ..queries import user_persons

MAX_BATCH = 200


def _refused(detail):
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(
    tags=["People"],
    summary="Get available actions for persons",
    request={
        "application/json": {
            "type": "object",
            "properties": {
                "uuids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                }
            },
            "required": ["uuids"],
        }
    },
    responses={
        200: OpenApiResponse(
            response=OpenApiTypes.OBJECT, description="Map of uuid to actions."
        ),
        400: OpenApiResponse(description="Invalid request."),
        404: OpenApiResponse(description="One or more UUIDs not found."),
    },
)
class PersonActionsView(APIView):
    def post(self, request):
        try:
            parsed = parse_uuid_batch(request.data, max_items=MAX_BATCH)
        except BatchTooLarge:
            return _refused(f"Too many UUIDs (max {MAX_BATCH}).")
        except MalformedUuid:
            return _refused("Malformed UUID in uuids.")
        except UuidBatchError:
            return _refused("uuids must be a non-empty list.")

        persons = list(user_persons(request.user).filter(uuid__in=parsed))
        result = actions_for(request.user, persons)
        if len(result) != len(set(parsed)):
            return Response(
                {"detail": "One or more UUIDs not found."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(result)
