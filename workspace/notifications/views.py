from django.conf import settings as django_settings
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.cache import invalidate_tags
from workspace.common.mixins import CacheControlMixin
from workspace.common.pagination import OptInLimitOffsetPagination
from workspace.common.url_guard import UnsafeUrl

from .models import Notification, PushSubscription
from .serializers import NotificationSerializer
from .services.notifications import _user_tag
from .services.push_subscriptions import save_push_subscription
from .services.push_transport import check_push_endpoint


@extend_schema(tags=["Notifications"])
class NotificationListView(CacheControlMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List notifications",
        description=(
            "Newest first. Paginated with ?limit and ?offset; the X-Has-More "
            "header says whether another page follows."
        ),
        responses={200: NotificationSerializer(many=True)},
    )
    def get(self, request):
        qs = (
            Notification.objects.filter(
                recipient=request.user,
            )
            .select_related("actor")
            .order_by("-created_at", "-uuid")
        )

        if request.query_params.get("filter") == "unread":
            qs = qs.filter(read_at__isnull=True)

        origin = request.query_params.get("origin")
        if origin:
            qs = qs.filter(origin=origin)

        search = request.query_params.get("search", "").strip()
        if search:
            qs = qs.filter(Q(title__ficontains=search) | Q(body__ficontains=search))

        paginator = OptInLimitOffsetPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is None:
            return Response(NotificationSerializer(qs, many=True).data)
        return paginator.get_paginated_response(
            NotificationSerializer(page, many=True).data
        )


@extend_schema(tags=["Notifications"])
class NotificationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark a notification as read")
    def patch(self, request, notification_id):
        """Mark a single notification as read."""
        try:
            notif = Notification.objects.get(
                uuid=notification_id, recipient=request.user
            )
        except Notification.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if notif.read_at is None:
            notif.read_at = timezone.now()
            notif.save(update_fields=["read_at"])
            invalidate_tags(_user_tag(request.user.id))
        return Response(NotificationSerializer(notif).data)

    @extend_schema(summary="Delete a notification")
    def delete(self, request, notification_id):
        """Delete a notification."""
        deleted, _ = Notification.objects.filter(
            uuid=notification_id,
            recipient=request.user,
        ).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        invalidate_tags(_user_tag(request.user.id))
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Notifications"])
class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Mark all notifications as read")
    def post(self, request):
        """Mark all unread notifications as read."""
        count = Notification.objects.filter(
            recipient=request.user,
            read_at__isnull=True,
        ).update(read_at=timezone.now())
        invalidate_tags(_user_tag(request.user.id))
        return Response({"marked": count})


@extend_schema(tags=["Notifications"])
class PushVapidKeyView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get VAPID public key for push subscriptions")
    def get(self, request):
        """Return the VAPID public key for push subscription."""
        return Response(
            {
                "public_key": getattr(django_settings, "WEBPUSH_VAPID_PUBLIC_KEY", ""),
            }
        )


@extend_schema(tags=["Notifications"])
class PushSubscribeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Subscribe to push notifications",
        request=inline_serializer(
            "PushSubscribe",
            fields={
                "endpoint": serializers.URLField(
                    help_text="Push service endpoint URL."
                ),
                "keys": inline_serializer(
                    "PushKeys",
                    fields={
                        "p256dh": serializers.CharField(),
                        "auth": serializers.CharField(),
                    },
                ),
            },
        ),
    )
    def post(self, request):
        """Subscribe to push notifications."""
        endpoint = request.data.get("endpoint")
        keys = request.data.get("keys", {})
        p256dh = keys.get("p256dh") if isinstance(keys, dict) else None
        auth = keys.get("auth") if isinstance(keys, dict) else None

        if not all(isinstance(v, str) and v for v in (endpoint, p256dh, auth)):
            return Response(
                {"error": "Missing required fields: endpoint, keys.p256dh, keys.auth"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            check_push_endpoint(endpoint)
        except UnsafeUrl as exc:
            return Response(
                {"error": exc.user_message}, status=status.HTTP_400_BAD_REQUEST
            )

        if not save_push_subscription(request.user, endpoint, p256dh, auth):
            return Response(
                {"error": "This push endpoint is already registered."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Unsubscribe from push notifications",
        request=inline_serializer(
            "PushUnsubscribe",
            fields={
                "endpoint": serializers.URLField(
                    help_text="Push service endpoint URL to remove."
                ),
            },
        ),
    )
    def delete(self, request):
        """Unsubscribe from push notifications."""
        endpoint = request.data.get("endpoint")
        PushSubscription.objects.filter(
            user=request.user,
            endpoint=endpoint,
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
