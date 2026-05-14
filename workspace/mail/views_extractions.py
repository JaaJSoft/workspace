import logging

from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none
from workspace.mail.models import MailExtraction

logger = logging.getLogger(__name__)


class ExtractionDetailView(APIView):
    """DELETE /api/v1/mail/extractions/<uuid> - dismiss an extraction.

    Sets status=DISMISSED and, for kind=event, deletes the produced Event
    in the same transaction. Idempotent: already-dismissed rows return 204.
    """
    permission_classes = [IsAuthenticated]

    def delete(self, request, extraction_id):
        ex_uuid = parse_uuid_or_none(str(extraction_id))
        if ex_uuid is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            ex = MailExtraction.objects.select_related('mail_message__account').get(
                uuid=ex_uuid,
                mail_message__account__owner=request.user,
            )
        except MailExtraction.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            if ex.status != MailExtraction.Status.DISMISSED:
                ex.status = MailExtraction.Status.DISMISSED
                ex.save(update_fields=['status'])

            target = ex.target
            if ex.kind == MailExtraction.Kind.EVENT and target is not None:
                target.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)
