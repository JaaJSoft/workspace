import logging

from django.db.models import Count, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.logging import scrub

from .models import MailAccount, MailFolder, MailLabel, MailMessage, MailMessageLabel
from .serializers import (
    MailAccountCreateSerializer,
    MailAccountSerializer,
    MailAccountUpdateSerializer,
)

logger = logging.getLogger(__name__)


def _bulk_refresh_counts(targets, group_key, grouped_qs, field_map):
    """Apply a grouped aggregate onto many target rows via a single ``bulk_update``.

    Shared plumbing for ``_refresh_folders_counts_bulk`` and ``_refresh_label_counts``:
    both reduce to "GROUP BY FK_id on a source table, then push the aggregated
    counts back onto a set of target rows". This helper handles the generic
    part: build a ``{pk: row}`` lookup, zero-default rows absent from the
    grouped result, set ``updated_at`` manually, and ``bulk_update``.

    Args:
        targets: List of already-loaded target model instances. The helper
            does NOT re-fetch them - callers pass the objects they want
            written back. Empty list is a no-op.
        group_key: Field name present in each ``grouped_qs`` row that maps to
            the target's primary key (e.g. ``'folder_id'``, ``'label_id'``).
        grouped_qs: An iterable of dicts - typically a queryset evaluated via
            ``.values(group_key).annotate(...)``. Rows are matched to targets
            by ``row[group_key] == target.pk``.
        field_map: ``{aggregate_alias: target_field_name}``. Targets absent
            from ``grouped_qs`` receive ``0`` for every mapped field.

    Notes:
        - Django's ``bulk_update`` bypasses ``auto_now``; ``updated_at`` is set
          manually to preserve the semantics of the original ``save()`` path.
        - All ``targets`` must share the same model class.
    """
    if not targets:
        return
    by_pk = {row[group_key]: row for row in grouped_qs}
    now = timezone.now()
    for target in targets:
        data = by_pk.get(target.pk, {})
        for alias, field_name in field_map.items():
            setattr(target, field_name, data.get(alias, 0))
        target.updated_at = now
    type(targets[0]).objects.bulk_update(
        targets,
        list(field_map.values()) + ["updated_at"],
    )


def _refresh_folder_counts(folder):
    """Recompute message_count and unread_count for a single folder.

    Single-folder fast path: 1 aggregate + 1 UPDATE via ``save()``. For N
    folders, prefer ``_refresh_folders_counts_bulk`` which collapses the work
    into 2 queries total regardless of N.
    """
    counts = MailMessage.objects.filter(
        folder=folder,
        deleted_at__isnull=True,
    ).aggregate(
        message_count=Count("pk"),
        unread_count=Count("pk", filter=Q(is_read=False)),
    )
    folder.message_count = counts["message_count"]
    folder.unread_count = counts["unread_count"]
    folder.save(update_fields=["message_count", "unread_count", "updated_at"])


def _refresh_folders_counts_bulk(folder_ids):
    """Refresh message_count + unread_count for many folders in 2 queries.

    Replaces the naive ``for folder in ...: _refresh_folder_counts(folder)``
    pattern (2N queries) with a single ``GROUP BY folder_id`` aggregate and a
    single ``bulk_update`` via :func:`_bulk_refresh_counts`.
    """
    folder_ids = list(folder_ids)
    if not folder_ids:
        return
    grouped = (
        MailMessage.objects.filter(
            folder_id__in=folder_ids,
            deleted_at__isnull=True,
        )
        .values("folder_id")
        .annotate(
            msg_count=Count("pk"),
            unread_cnt=Count("pk", filter=Q(is_read=False)),
        )
    )
    folders = list(MailFolder.objects.filter(uuid__in=folder_ids))
    _bulk_refresh_counts(
        folders,
        "folder_id",
        grouped,
        {"msg_count": "message_count", "unread_cnt": "unread_count"},
    )


def _refresh_label_counts(labels):
    """Recompute unread_count for one or more labels in 2 queries.

    Accepts a single MailLabel, a queryset, or any iterable of MailLabels.
    Uses a single ``GROUP BY label_id`` aggregate + ``bulk_update`` regardless
    of N, instead of the previous 2N queries (1 COUNT + 1 UPDATE per label).
    """
    if isinstance(labels, MailLabel):
        labels = [labels]
    labels = list(labels)
    if not labels:
        return
    grouped = (
        MailMessageLabel.objects.filter(
            label_id__in=[lbl.pk for lbl in labels],
            message__is_read=False,
            message__deleted_at__isnull=True,
        )
        .values("label_id")
        .annotate(
            unread_cnt=Count("pk"),
        )
    )
    _bulk_refresh_counts(
        labels,
        "label_id",
        grouped,
        {"unread_cnt": "unread_count"},
    )


def _refresh_message_label_counts(message):
    """Refresh unread counts for all labels attached to a message."""
    label_ids = MailMessageLabel.objects.filter(message=message).values_list(
        "label_id", flat=True
    )
    if label_ids:
        _refresh_label_counts(MailLabel.objects.filter(pk__in=label_ids))


@extend_schema(tags=["Mail"])
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


@extend_schema(tags=["Mail"])
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


@extend_schema(tags=["Mail"])
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


@extend_schema(tags=["Mail"])
class MailAccountTestView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Test IMAP and SMTP connections for an account")
    def post(self, request, uuid):
        try:
            account = MailAccount.objects.get(uuid=uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap_connection import test_imap_connection
        from .services.smtp import test_smtp_connection

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


@extend_schema(tags=["Mail"])
class MailAccountSyncView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Trigger sync for a mail account")
    def post(self, request, uuid):
        try:
            account = MailAccount.objects.get(uuid=uuid, owner=request.user)
        except MailAccount.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        from .services.imap_sync import sync_account

        try:
            sync_account(account)
            return Response({"status": "ok", "last_sync_at": account.last_sync_at})
        except Exception as e:
            account.last_sync_error = str(e)
            account.save(update_fields=["last_sync_error", "updated_at"])
            logger.exception("Failed to sync account %s", scrub(account.email))
            return Response(
                {"status": "error", "error": "Sync failed"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
