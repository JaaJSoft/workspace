from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from workspace.common.uuids import parse_uuid_or_none

from ..queries import user_account_ids
from ..services.recipients import recipient_suggestions


@extend_schema(tags=["Mail - Messages"])
class ContactAutocompleteView(CacheControlMixin, APIView):
    # Short: a contact added from the dropdown must show up on the next search.
    cache_max_age = 60
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Suggest recipients",
        description=(
            "Persons of the address book (`kind: person`, every email), then "
            "workspace accounts (`kind: account`), then correspondents derived "
            "from message history (`kind: history`). An address appears once, "
            "under the first kind that has it."
        ),
        parameters=[
            OpenApiParameter(
                "q", str, required=True, description="Search query (min 2 chars)"
            ),
            OpenApiParameter(
                "account_id",
                str,
                required=False,
                description="Only derive history from this account",
            ),
        ],
    )
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if len(q) < 2:
            return Response([])

        account_filter = Q(account_id__in=user_account_ids(request.user))
        account_id = request.query_params.get("account_id")
        if account_id:
            account_uuid = parse_uuid_or_none(account_id)
            if account_uuid is None:
                return Response(
                    {"detail": '"account_id" must be a valid UUID.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            account_filter &= Q(account__uuid=account_uuid)

        return Response(recipient_suggestions(request.user, q, account_filter))
