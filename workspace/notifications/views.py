from django.conf import settings as django_settings
from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification, PushSubscription
from .serializers import NotificationSerializer


@extend_schema(tags=['Notifications'])
class NotificationListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = Notification.objects.filter(
            recipient=request.user,
        ).select_related('actor').order_by('-created_at')

        # Filter: unread only
        if request.query_params.get('filter') == 'unread':
            qs = qs.filter(read_at__isnull=True)

        # Filter: by origin
        origin = request.query_params.get('origin')
        if origin:
            qs = qs.filter(origin=origin)

        # Search: title or body
        search = request.query_params.get('search', '').strip()
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(body__icontains=search))

        # Simple cursor pagination via ?before=<uuid>
        before = request.query_params.get('before')
        if before:
            try:
                cursor_notif = Notification.objects.get(uuid=before)
                qs = qs.filter(created_at__lt=cursor_notif.created_at)
            except Notification.DoesNotExist:
                pass

        limit = min(int(request.query_params.get('limit', 20)), 50)
        notifications = list(qs[:limit + 1])
        has_more = len(notifications) > limit
        notifications = notifications[:limit]

        return Response({
            'notifications': NotificationSerializer(notifications, many=True).data,
            'has_more': has_more,
            'unread_count': Notification.objects.filter(
                recipient=request.user, read_at__isnull=True,
            ).count(),
        })


@extend_schema(tags=['Notifications'])
class NotificationDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, notification_id):
        """Mark a single notification as read."""
        try:
            notif = Notification.objects.get(uuid=notification_id, recipient=request.user)
        except Notification.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        if notif.read_at is None:
            notif.read_at = timezone.now()
            notif.save(update_fields=['read_at'])
        return Response(NotificationSerializer(notif).data)

    def delete(self, request, notification_id):
        """Delete a notification."""
        deleted, _ = Notification.objects.filter(
            uuid=notification_id, recipient=request.user,
        ).delete()
        if not deleted:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=['Notifications'])
class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Mark all unread notifications as read."""
        count = Notification.objects.filter(
            recipient=request.user, read_at__isnull=True,
        ).update(read_at=timezone.now())
        return Response({'marked': count})


@extend_schema(tags=['Notifications'])
class PushVapidKeyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        """Return the VAPID public key for push subscription."""
        return Response({
            'public_key': getattr(django_settings, 'WEBPUSH_VAPID_PUBLIC_KEY', ''),
        })


@extend_schema(tags=['Notifications'])
class PushSubscribeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Subscribe to push notifications."""
        endpoint = request.data.get('endpoint')
        keys = request.data.get('keys', {})
        p256dh = keys.get('p256dh') if isinstance(keys, dict) else None
        auth = keys.get('auth') if isinstance(keys, dict) else None

        if not endpoint or not p256dh or not auth:
            return Response(
                {'error': 'Missing required fields: endpoint, keys.p256dh, keys.auth'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                'user': request.user,
                'p256dh': p256dh,
                'auth': auth,
            },
        )
        return Response(status=status.HTTP_201_CREATED)

    def delete(self, request):
        """Unsubscribe from push notifications."""
        endpoint = request.data.get('endpoint')
        PushSubscription.objects.filter(
            user=request.user,
            endpoint=endpoint,
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
