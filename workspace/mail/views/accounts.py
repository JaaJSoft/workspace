import logging

from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.logging import scrub
from workspace.common.task_priority import INTERACTIVE_PRIORITY

from ..models import MailAccount
from ..serializers import (
    MailAccountCreateSerializer,
    MailAccountSerializer,
    MailAccountUpdateSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(tags=["Mail - Accounts"])
class MailAutodiscoverView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Auto-discover IMAP/SMTP settings for an email address",
        request=inline_serializer(
            "MailAutodiscover",
            fields={
                "email": serializers.EmailField(
                    help_text="Email address to discover settings for."
                ),
            },
        ),
    )
    def post(self, request):
        email = (request.data.get("email") or "").strip()
        if not email or "@" not in email:
            return Response(
                {"detail": "A valid email address is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        domain = email.split("@", 1)[1]

        from myldiscovery import autodiscover

        try:
            settings = autodiscover(domain)
        except Exception:
            logger.info("Autodiscover failed for domain %s", scrub(domain))
            settings = None

        if not settings or not settings.get("imap") or not settings.get("smtp"):
            return Response(
                {"detail": "Could not auto-detect settings for this domain"},
                status=status.HTTP_404_NOT_FOUND,
            )

        imap = settings["imap"]
        smtp = settings["smtp"]

        # Map starttls to use_ssl / use_tls
        imap_use_ssl = not imap.get("starttls", False)
        smtp_use_tls = smtp.get("starttls", True)

        return Response(
            {
                "imap_host": imap.get("server", ""),
                "imap_port": imap.get("port", 993),
                "imap_use_ssl": imap_use_ssl,
                "smtp_host": smtp.get("server", ""),
                "smtp_port": smtp.get("port", 587),
                "smtp_use_tls": smtp_use_tls,
            }
        )


@extend_schema(tags=["Mail - Accounts"])
class MailAccountListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List user's mail accounts")
    def get(self, request):
        accounts = MailAccount.objects.filter(owner=request.user)
        return Response(MailAccountSerializer(accounts, many=True).data)

    @extend_schema(summary="Add a mail account", request=MailAccountCreateSerializer)
    def post(self, request):
        ser = MailAccountCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        password = d.pop("password")
        account = MailAccount(owner=request.user, **d)
        account.set_password(password)
        account.save()

        return Response(
            MailAccountSerializer(account).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Mail - Accounts"])
class MailAccountDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_account(self, request, uuid):
        try:
            return MailAccount.objects.get(uuid=uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return None

    @extend_schema(summary="Get mail account details")
    def get(self, request, uuid):
        account = self._get_account(request, uuid)
        if not account:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(MailAccountSerializer(account).data)

    @extend_schema(summary="Update a mail account", request=MailAccountUpdateSerializer)
    def patch(self, request, uuid):
        account = self._get_account(request, uuid)
        if not account:
            return Response(status=status.HTTP_404_NOT_FOUND)

        ser = MailAccountUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        password = ser.validated_data.pop("password", None)
        for key, value in ser.validated_data.items():
            setattr(account, key, value)
        if password:
            account.set_password(password)
        account.save()

        return Response(MailAccountSerializer(account).data)

    @extend_schema(summary="Delete a mail account")
    def delete(self, request, uuid):
        account = self._get_account(request, uuid)
        if not account:
            return Response(status=status.HTTP_404_NOT_FOUND)
        account.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Mail - Accounts"])
class MailAccountTestView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Test IMAP and SMTP connections for an account")
    def post(self, request, uuid):
        try:
            account = MailAccount.objects.get(uuid=uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from ..services.imap_connection import test_imap_connection
        from ..services.smtp import test_smtp_connection

        imap_ok, imap_error = test_imap_connection(account)
        smtp_ok, smtp_error = test_smtp_connection(account)

        if imap_error:
            logger.warning(
                "IMAP test failed for %s: %s", scrub(account.email), scrub(imap_error)
            )
        if smtp_error:
            logger.warning(
                "SMTP test failed for %s: %s", scrub(account.email), scrub(smtp_error)
            )

        return Response(
            {
                "imap": {
                    "success": imap_ok,
                    "error": None if imap_ok else "Connection failed",
                },
                "smtp": {
                    "success": smtp_ok,
                    "error": None if smtp_ok else "Connection failed",
                },
            }
        )


@extend_schema(tags=["Mail - Accounts"])
class MailAccountSyncView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Queue a sync for a mail account",
        description=(
            "Answers 202 once the sync is queued. The sync is over when the "
            "account's `updated_at` moves past the returned one; "
            "`last_sync_at` and `last_sync_error` then carry its outcome."
        ),
    )
    def post(self, request, uuid):
        try:
            account = MailAccount.objects.get(uuid=uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if not account.is_active:
            return Response(
                {"detail": "Account is inactive"}, status=status.HTTP_409_CONFLICT
            )

        from ..tasks import sync_single_account

        # Read before queueing: under eager Celery the sync has already run
        # (and moved updated_at) by the time apply_async returns.
        updated_at = MailAccountSerializer(account).data["updated_at"]
        try:
            sync_single_account.apply_async(
                args=[str(account.uuid)], priority=INTERACTIVE_PRIORITY
            )
        except Exception:
            logger.exception("Failed to queue sync for %s", scrub(account.email))
            return Response(
                {"status": "error", "error": "Sync could not be queued"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {"status": "queued", "updated_at": updated_at},
            status=status.HTTP_202_ACCEPTED,
        )
