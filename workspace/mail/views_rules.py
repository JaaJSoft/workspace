import logging

from django.db import transaction
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.uuids import parse_uuid_or_none

from .models import MailAccount, MailFolder, MailMessage, MailRule, MailRuleLog
from .queries import user_account_ids
from .serializers import (
    MailRuleApplySerializer,
    MailRuleCreateSerializer,
    MailRuleLogSerializer,
    MailRuleReorderSerializer,
    MailRuleSerializer,
    MailRuleTestSerializer,
    MailRuleUpdateSerializer,
)
from .services.rules.conditions import evaluate_node
from .services.rules.engine import apply_rule_to_folder
from .services.rules.schema import SchemaError, parse_conditions

logger = logging.getLogger(__name__)


def _get_user_account(request, account_id):
    """Return (account, error_response). One of them is None."""
    if not account_id:
        return None, Response(
            {"detail": "account query parameter is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    account_uuid = parse_uuid_or_none(account_id)
    if account_uuid is None:
        return None, Response(
            {"detail": "account must be a valid UUID"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        account = MailAccount.objects.get(uuid=account_uuid, owner=request.user)
    except MailAccount.DoesNotExist:
        return None, Response(status=status.HTTP_404_NOT_FOUND)
    return account, None


def _get_user_rule(request, uuid):
    try:
        rule = MailRule.objects.select_related("account").get(uuid=uuid)
    except MailRule.DoesNotExist:
        return None
    if rule.account.owner_id != request.user.pk:
        return None
    return rule


@extend_schema(tags=["Mail"])
class MailRuleListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List rules for a mail account",
        parameters=[OpenApiParameter("account", str, required=True)],
    )
    def get(self, request):
        account, err = _get_user_account(request, request.query_params.get("account"))
        if err:
            return err
        rules = MailRule.objects.filter(account=account).order_by(
            "position", "created_at"
        )
        return Response(MailRuleSerializer(rules, many=True).data)

    @extend_schema(summary="Create a rule", request=MailRuleCreateSerializer)
    def post(self, request):
        ser = MailRuleCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        try:
            account = MailAccount.objects.get(
                uuid=data["account_id"], owner=request.user
            )
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Default to end of the list so newly-created rules don't stack at
        # position 0 (which causes ordering ambiguity and breaks the
        # up/down move buttons that rely on `position` being contiguous).
        position = data.get("position")
        if position is None or position == 0:
            position = MailRule.objects.filter(account=account).count()
        rule = MailRule.objects.create(
            account=account,
            name=data["name"],
            is_enabled=data.get("is_enabled", True),
            stop_processing=data.get("stop_processing", False),
            position=position,
            conditions=data["conditions"],
            actions=data["actions"],
        )
        return Response(MailRuleSerializer(rule).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["Mail"])
class MailRuleDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get rule details")
    def get(self, request, uuid):
        rule = _get_user_rule(request, uuid)
        if not rule:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(MailRuleSerializer(rule).data)

    @extend_schema(summary="Update a rule", request=MailRuleUpdateSerializer)
    def patch(self, request, uuid):
        rule = _get_user_rule(request, uuid)
        if not rule:
            return Response(status=status.HTTP_404_NOT_FOUND)
        ser = MailRuleUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        update_fields = ["updated_at"]
        for key, value in ser.validated_data.items():
            setattr(rule, key, value)
            update_fields.append(key)
        rule.save(update_fields=update_fields)
        return Response(MailRuleSerializer(rule).data)

    @extend_schema(summary="Delete a rule")
    def delete(self, request, uuid):
        rule = _get_user_rule(request, uuid)
        if not rule:
            return Response(status=status.HTTP_404_NOT_FOUND)
        rule.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Mail"])
class MailRuleReorderView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Move a rule to a new position", request=MailRuleReorderSerializer
    )
    def post(self, request, uuid):
        rule = _get_user_rule(request, uuid)
        if not rule:
            return Response(status=status.HTTP_404_NOT_FOUND)
        ser = MailRuleReorderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        target = ser.validated_data["position"]

        with transaction.atomic():
            siblings = list(
                MailRule.objects.select_for_update()
                .filter(account=rule.account)
                .exclude(pk=rule.pk)
                .order_by("position", "created_at")
            )
            siblings.insert(min(target, len(siblings)), rule)
            for index, item in enumerate(siblings):
                if item.position != index:
                    item.position = index
            MailRule.objects.bulk_update(siblings, ["position"])

        rule.refresh_from_db()
        return Response(MailRuleSerializer(rule).data)


@extend_schema(tags=["Mail"])
class MailRuleTestView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Dry-run a condition tree against an existing message",
        request=MailRuleTestSerializer,
    )
    def post(self, request):
        ser = MailRuleTestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            message = MailMessage.objects.select_related("folder").get(
                uuid=ser.validated_data["message_id"],
                account_id__in=user_account_ids(request.user),
                deleted_at__isnull=True,
            )
        except MailMessage.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if "rule_id" in ser.validated_data:
            try:
                rule = MailRule.objects.select_related("account").get(
                    uuid=ser.validated_data["rule_id"],
                )
            except MailRule.DoesNotExist:
                return Response(status=status.HTTP_404_NOT_FOUND)
            if rule.account.owner_id != request.user.pk:
                return Response(status=status.HTTP_404_NOT_FOUND)
            conditions = rule.conditions
        else:
            conditions = ser.validated_data["conditions"]

        try:
            node = parse_conditions(conditions)
            matched = evaluate_node(node, message)
        except SchemaError:
            # Avoid surfacing the raw Pydantic exception (CodeQL info-exposure).
            return Response(
                {"detail": "Invalid conditions payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response({"matched": matched, "message_id": str(message.uuid)})


@extend_schema(tags=["Mail"])
class MailRuleLogsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Paginated audit logs for a rule",
        parameters=[OpenApiParameter("page", int, required=False)],
    )
    def get(self, request, uuid):
        rule = _get_user_rule(request, uuid)
        if not rule:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            page = int(request.query_params.get("page", 1))
            if page < 1:
                page = 1
        except (TypeError, ValueError):
            page = 1
        page_size = 50
        offset = (page - 1) * page_size

        qs = MailRuleLog.objects.filter(rule=rule).select_related("message")
        total = qs.count()
        logs = qs.order_by("-created_at")[offset : offset + page_size]
        return Response(
            {
                "results": MailRuleLogSerializer(logs, many=True).data,
                "count": total,
                "page": page,
                "page_size": page_size,
            }
        )


@extend_schema(tags=["Mail"])
class MailRuleApplyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Apply a rule to all messages in a folder (preview or real)",
        request=MailRuleApplySerializer,
    )
    def post(self, request, uuid):
        rule = _get_user_rule(request, uuid)
        if not rule:
            return Response(status=status.HTTP_404_NOT_FOUND)
        ser = MailRuleApplySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            folder = MailFolder.objects.get(
                uuid=ser.validated_data["folder_id"],
                account=rule.account,
            )
        except MailFolder.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        result = apply_rule_to_folder(
            rule,
            folder,
            dry_run=ser.validated_data["dry_run"],
        )
        return Response(result)
