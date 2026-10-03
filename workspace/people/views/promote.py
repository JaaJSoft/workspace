from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers import PersonPromoteSerializer, PersonSerializer
from ..services.persons import promote_to_person


class PersonPromoteView(APIView):
    @extend_schema(
        tags=["People"],
        summary="Add a correspondent or an account to the address book",
        description=(
            "Returns the person already carrying the email or linked to the "
            "account (200), or the one created for it in the user's own "
            "address book (201)."
        ),
        request=PersonPromoteSerializer,
        responses={
            200: PersonSerializer,
            201: PersonSerializer,
            400: OpenApiResponse(description="Neither or both of email and user_id."),
        },
    )
    def post(self, request):
        serializer = PersonPromoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        person, created = promote_to_person(request.user, **serializer.validated_data)
        data = PersonSerializer(person, context={"request": request}).data
        return Response(
            data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )
