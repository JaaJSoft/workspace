import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none

from .models import MailAccount, MailLabel, MailMessage, MailMessageLabel
from .serializers import (
    MailLabelAssignSerializer,
    MailLabelCreateSerializer,
    MailLabelSerializer,
    MailLabelUpdateSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["Mail"])
class MailLabelListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List labels for a mail account",
        parameters=[OpenApiParameter("account", str, required=True)],
    )
    def get(self, request):
        account_id = request.query_params.get("account")
        if not account_id:
            return Response(
                {"detail": "account query parameter is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        account_uuid = parse_uuid_or_none(account_id)
        if account_uuid is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            account = MailAccount.objects.get(uuid=account_uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        labels = MailLabel.objects.filter(account=account)
        return Response(MailLabelSerializer(labels, many=True).data)

    @extend_schema(summary="Create a label", request=MailLabelCreateSerializer)
    def post(self, request):
        ser = MailLabelCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            account = MailAccount.objects.get(
                uuid=ser.validated_data["account_id"],
                owner=request.user,
            )
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if MailLabel.objects.filter(
            account=account, name=ser.validated_data["name"]
        ).exists():
            return Response(
                {"detail": "A label with this name already exists for this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        label = MailLabel.objects.create(
            account=account,
            name=ser.validated_data["name"],
            color=ser.validated_data.get("color", ""),
            icon=ser.validated_data.get("icon", ""),
        )
        return Response(MailLabelSerializer(label).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Mail"])
class MailLabelDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_label(self, request, uuid):
        try:
            label = MailLabel.objects.select_related("account").get(uuid=uuid)
        except MailLabel.DoesNotExist:
            return None
        if label.account.owner != request.user:
            return None
        return label

    @extend_schema(summary="Update a label", request=MailLabelUpdateSerializer)
    def patch(self, request, uuid):
        label = self._get_label(request, uuid)
        if not label:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ser = MailLabelUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        new_name = ser.validated_data.get("name")
        if new_name and new_name != label.name:
            if MailLabel.objects.filter(account=label.account, name=new_name).exists():
                return Response(
                    {
                        "detail": "A label with this name already exists for this account."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        update_fields = ["updated_at"]
        for field in ("name", "color", "icon", "position"):
            if field in ser.validated_data:
                setattr(label, field, ser.validated_data[field])
                update_fields.append(field)
        if len(update_fields) > 1:
            label.save(update_fields=update_fields)

        return Response(MailLabelSerializer(label).data)

    @extend_schema(summary="Delete a label")
    def delete(self, request, uuid):
        label = self._get_label(request, uuid)
        if not label:
            return Response(status=status.HTTP_404_NOT_FOUND)
        label.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Mail"])
class MailMessageLabelView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_message(self, request, uuid):
        try:
            msg = MailMessage.objects.select_related("account").get(uuid=uuid)
        except MailMessage.DoesNotExist:
            return None
        if msg.account.owner != request.user:
            return None
        return msg

    @extend_schema(
        summary="Assign labels to a message", request=MailLabelAssignSerializer
    )
    @transaction.atomic
    def post(self, request, uuid):
        msg = self._get_message(request, uuid)
        if not msg:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ser = MailLabelAssignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        labels = MailLabel.objects.filter(
            uuid__in=ser.validated_data["label_ids"],
            account=msg.account,
        )
        if labels.count() != len(ser.validated_data["label_ids"]):
            return Response(
                {"detail": "One or more labels do not belong to this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        MailMessageLabel.objects.bulk_create(
            [MailMessageLabel(message=msg, label=lbl) for lbl in labels],
            ignore_conflicts=True,
        )
        from .views import _refresh_label_counts

        _refresh_label_counts(labels)
        return Response({"status": "ok"})

    @extend_schema(
        summary="Remove labels from a message", request=MailLabelAssignSerializer
    )
    @transaction.atomic
    def delete(self, request, uuid):
        msg = self._get_message(request, uuid)
        if not msg:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ser = MailLabelAssignSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        affected_labels = list(
            MailLabel.objects.filter(
                pk__in=ser.validated_data["label_ids"],
                account=msg.account,
            )
        )
        # Mirror post(): reject unknown / cross-account label ids instead of
        # silently ignoring them — keeps the API symmetrical and helps catch
        # client-side bugs early.
        if len(affected_labels) != len(ser.validated_data["label_ids"]):
            return Response(
                {"detail": "One or more labels do not belong to this account."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        MailMessageLabel.objects.filter(
            message=msg,
            label_id__in=ser.validated_data["label_ids"],
        ).delete()
        from .views import _refresh_label_counts

        _refresh_label_counts(affected_labels)
        return Response({"status": "ok"})
