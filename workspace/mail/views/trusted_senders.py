from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ..serializers import MailTrustedSenderSerializer
from ..services.trusted_senders import distrust_sender, trust_sender


@extend_schema(tags=["Mail - Messages"])
class MailTrustedSenderView(APIView):
    """The senders whose messages show their remote images without asking."""

    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Trust a sender", request=MailTrustedSenderSerializer)
    def post(self, request):
        ser = MailTrustedSenderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        trust_sender(request.user, ser.validated_data["email"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        summary="Stop trusting a sender", request=MailTrustedSenderSerializer
    )
    def delete(self, request):
        ser = MailTrustedSenderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        distrust_sender(request.user, ser.validated_data["email"])
        return Response(status=status.HTTP_204_NO_CONTENT)
