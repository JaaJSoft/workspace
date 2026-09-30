from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers import InviteeResolveSerializer, ResolvedInviteesSerializer
from ..services.address_book import resolve_invitees


@extend_schema(tags=["Calendar"])
class InviteeResolveView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Resolve address-book persons and lists into event invitees",
        description=(
            "Persons linked to a workspace account resolve to that account, "
            "any other person to an external guest on their first email. "
            "Persons with neither come back in `skipped`. Unreachable ids are "
            "ignored."
        ),
        request=InviteeResolveSerializer,
        responses=ResolvedInviteesSerializer,
    )
    def post(self, request):
        ser = InviteeResolveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        resolved = resolve_invitees(
            request.user,
            person_ids=ser.validated_data["person_ids"],
            list_ids=ser.validated_data["list_ids"],
        )
        return Response(ResolvedInviteesSerializer(resolved).data)
