from django.conf import settings as django_settings
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.generics import (
    DestroyAPIView,
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
    RetrieveUpdateDestroyAPIView,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.exceptions import Conflict
from workspace.common.mixins import CacheControlMixin

from .filters import NotificationFilter
from .models import Notification, PushSubscription
from .queries import user_notifications, user_push_subscriptions
from .serializers import (
    NotificationSerializer,
    PushKeySerializer,
    PushSubscriptionSerializer,
)
from .services.notifications import (
    delete_notification,
    mark_all_read,
    mark_read,
    mark_unread,
)
from .services.push_subscriptions import save_push_subscription


@extend_schema(tags=["Notifications"])
@extend_schema_view(get=extend_schema(summary="List notifications, newest first"))
class NotificationListView(CacheControlMixin, ListAPIView):
    permission_classes = [IsAuthenticated]
    queryset = Notification.objects.none()
    serializer_class = NotificationSerializer
    filterset_class = NotificationFilter

    def get_queryset(self):
        return user_notifications(self.request.user)


@extend_schema(tags=["Notifications"])
@extend_schema_view(
    get=extend_schema(summary="Get a notification"),
    patch=extend_schema(summary="Mark a notification read or unread"),
    delete=extend_schema(summary="Delete a notification"),
)
class NotificationDetailView(RetrieveUpdateDestroyAPIView):
    permission_classes = [IsAuthenticated]
    queryset = Notification.objects.none()
    serializer_class = NotificationSerializer
    lookup_field = "uuid"
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return user_notifications(self.request.user)

    def perform_update(self, serializer):
        is_read = serializer.validated_data.get("is_read")
        if is_read is True:
            mark_read(serializer.instance)
        elif is_read is False:
            mark_unread(serializer.instance)

    def perform_destroy(self, instance):
        delete_notification(instance)


@extend_schema(tags=["Notifications"])
class NotificationMarkAllReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Mark all notifications as read", request=None, responses={204: None}
    )
    def post(self, request):
        mark_all_read(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Notifications"])
@extend_schema_view(
    get=extend_schema(summary="Get the VAPID public key a browser subscribes with")
)
class PushVapidKeyView(RetrieveAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PushKeySerializer

    def get_object(self):
        return {"public_key": getattr(django_settings, "WEBPUSH_VAPID_PUBLIC_KEY", "")}


@extend_schema(tags=["Notifications"])
@extend_schema_view(
    get=extend_schema(summary="List the browsers registered for push"),
    post=extend_schema(
        summary="Register a browser for push",
        description=(
            "Registering an endpoint again refreshes its keys and answers 200 "
            "instead of 201. An endpoint held by another account moves to the "
            "caller only with the keys stored for it, and answers 409 without."
        ),
        responses={
            200: PushSubscriptionSerializer,
            201: PushSubscriptionSerializer,
        },
    ),
)
class PushSubscriptionListView(ListCreateAPIView):
    permission_classes = [IsAuthenticated]
    queryset = PushSubscription.objects.none()
    serializer_class = PushSubscriptionSerializer
    # One row per browser the user enabled push in.
    pagination_class = None

    def get_queryset(self):
        return user_push_subscriptions(self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        keys = serializer.validated_data["keys"]
        saved = save_push_subscription(
            request.user,
            serializer.validated_data["endpoint"],
            keys["p256dh"],
            keys["auth"],
        )
        if saved is None:
            raise Conflict("This push endpoint is already registered.")
        subscription, created = saved
        return Response(
            self.get_serializer(subscription).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(tags=["Notifications"])
@extend_schema_view(delete=extend_schema(summary="Stop pushing to a browser"))
class PushSubscriptionDetailView(DestroyAPIView):
    permission_classes = [IsAuthenticated]
    queryset = PushSubscription.objects.none()
    serializer_class = PushSubscriptionSerializer
    lookup_field = "uuid"

    def get_queryset(self):
        return user_push_subscriptions(self.request.user)
