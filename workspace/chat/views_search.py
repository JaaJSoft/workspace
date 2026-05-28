import logging
from datetime import timedelta

from django.db.models import Count, F, Max, Min, Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.common.mixins import CacheControlMixin
from .models import Message, MessageAttachment, Reaction
from .services.conversations import get_active_membership

logger = logging.getLogger(__name__)


@extend_schema(tags=['Chat'])
class ConversationMessageSearchView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Search messages in a conversation",
        parameters=[
            OpenApiParameter(name='q', type=str, required=False),
            OpenApiParameter(name='author', type=int, required=False),
            OpenApiParameter(name='date_range', type=str, required=False, enum=['today', '7d', '30d']),
            OpenApiParameter(name='date_from', type=str, required=False, description='ISO date (YYYY-MM-DD)'),
            OpenApiParameter(name='date_to', type=str, required=False, description='ISO date (YYYY-MM-DD)'),
            OpenApiParameter(name='has_files', type=bool, required=False),
            OpenApiParameter(name='has_images', type=bool, required=False),
        ],
    )
    def get(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        params = request.query_params
        query = params.get('q', '').strip()
        author_id = params.get('author', '').strip()
        date_range = params.get('date_range', '').strip()
        date_from = params.get('date_from', '').strip()
        date_to = params.get('date_to', '').strip()
        has_files = params.get('has_files', '').lower() == 'true'
        has_images = params.get('has_images', '').lower() == 'true'

        has_any = query or author_id or date_range or date_from or date_to or has_files or has_images
        if not has_any:
            return Response(
                {'detail': 'At least one search criterion is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        qs = Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        )

        if query:
            qs = qs.filter(body__icontains=query)

        if author_id:
            try:
                qs = qs.filter(author_id=int(author_id))
            except ValueError:
                return Response(
                    {'detail': 'Invalid author id.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        now = timezone.now()
        if date_range == 'today':
            qs = qs.filter(created_at__date=now.date())
        elif date_range == '7d':
            qs = qs.filter(created_at__gte=now - timedelta(days=7))
        elif date_range == '30d':
            qs = qs.filter(created_at__gte=now - timedelta(days=30))
        elif date_range:
            # Reject unknown values rather than silently treating them as
            # "no filter".
            return Response(
                {'detail': 'Invalid date_range.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate ISO dates up front so the ORM never sees garbage like
        # ?date_from=invalid (which would 500).
        from django.utils.dateparse import parse_date
        if date_from:
            parsed = parse_date(date_from)
            if parsed is None:
                return Response(
                    {'detail': 'Invalid date_from.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(created_at__date__gte=parsed)
        if date_to:
            parsed = parse_date(date_to)
            if parsed is None:
                return Response(
                    {'detail': 'Invalid date_to.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            qs = qs.filter(created_at__date__lte=parsed)

        if has_files:
            qs = qs.filter(attachments__isnull=False)
        if has_images:
            qs = qs.filter(attachments__mime_type__startswith='image/')

        messages = (
            qs.select_related('author')
            .order_by('-created_at')
            .distinct()[:50]
        )

        results = [
            {
                'uuid': str(msg.uuid),
                'author': {
                    'id': msg.author.id,
                    'username': msg.author.username,
                },
                'body': msg.body,
                'body_html': msg.body_html,
                'created_at': msg.created_at.isoformat(),
            }
            for msg in messages
        ]

        return Response({
            'results': results,
            'query': query,
            'count': len(results),
        })


@extend_schema(tags=['Chat'])
class ConversationMediaView(CacheControlMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="List media attachments in a conversation",
        parameters=[
            OpenApiParameter('type', str, enum=['images', 'files', 'all'], default='images',
                             description='Filter by attachment type.'),
            OpenApiParameter('offset', int, default=0),
            OpenApiParameter('limit', int, default=24),
        ],
    )
    def get(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        qs = MessageAttachment.objects.filter(
            message__conversation_id=conversation_id,
            message__deleted_at__isnull=True,
        ).select_related('message__author').order_by('-created_at')

        media_type = request.query_params.get('type', 'images')
        if media_type == 'images':
            qs = qs.filter(Q(mime_type__startswith='image/') | Q(mime_type__startswith='video/'))
        elif media_type == 'files':
            qs = qs.exclude(mime_type__startswith='image/').exclude(mime_type__startswith='video/')
        elif media_type != 'all':
            return Response(
                {'detail': 'Invalid media type.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        total = qs.count()
        try:
            offset = max(int(request.query_params.get('offset', 0)), 0)
            limit = min(max(int(request.query_params.get('limit', 24)), 0), 100)
        except (TypeError, ValueError):
            return Response(
                {'detail': 'Invalid pagination parameters.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        items = qs[offset:offset + limit]

        data = []
        for att in items:
            author = att.message.author
            data.append({
                'uuid': att.uuid,
                'original_name': att.original_name,
                'mime_type': att.mime_type,
                'type': att.type,
                'size': att.size,
                'is_image': att.is_image,
                'is_video': att.is_video,
                'url': f'/api/v1/chat/attachments/{att.uuid}',
                'created_at': att.created_at.isoformat(),
                'message_uuid': att.message_id,
                'author': {
                    'id': author.id,
                    'username': author.username,
                    'first_name': author.first_name,
                    'last_name': author.last_name,
                },
            })

        return Response({
            'results': data,
            'total': total,
            'offset': offset,
            'limit': limit,
        })


@extend_schema(tags=['Chat'])
class ConversationStatsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get conversation statistics")
    def get(self, request, conversation_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {'detail': 'Not a member of this conversation.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        active_messages = Message.objects.filter(
            conversation_id=conversation_id,
            deleted_at__isnull=True,
        )

        aggregates = active_messages.aggregate(
            message_count=Count('uuid'),
            first_message_at=Min('created_at'),
            last_message_at=Max('created_at'),
        )

        reaction_count = Reaction.objects.filter(
            message__conversation_id=conversation_id,
            message__deleted_at__isnull=True,
        ).count()

        messages_per_member = list(
            active_messages
            .values(username=F('author__username'))
            .annotate(count=Count('uuid'))
            .order_by('-count')
        )

        return Response({
            'message_count': aggregates['message_count'],
            'reaction_count': reaction_count,
            'first_message_at': aggregates['first_message_at'],
            'last_message_at': aggregates['last_message_at'],
            'messages_per_member': messages_per_member,
        })
