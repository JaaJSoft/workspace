from datetime import date

from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework.response import Response
from rest_framework.views import APIView

from workspace.core.activity_registry import activity_registry
from workspace.core.services.activity import get_recent_events, get_sources, serialize_timestamps


class ActivityRecentView(APIView):
    @extend_schema(
        tags=['Activity'],
        summary='Recent activity events',
        parameters=[
            OpenApiParameter(name='user_id', type=int, required=False),
            OpenApiParameter(name='source', type=str, required=False),
            OpenApiParameter(name='limit', type=int, required=False),
            OpenApiParameter(name='offset', type=int, required=False),
        ],
    )
    def get(self, request):
        user_id = request.query_params.get('user_id')
        if user_id:
            user_id = int(user_id)
            viewer_id = request.user.id
        else:
            user_id = request.user.id
            viewer_id = None

        source = request.query_params.get('source')

        try:
            limit = int(request.query_params.get('limit', 10))
        except (TypeError, ValueError):
            limit = 10
        limit = max(1, min(limit, 50))

        try:
            offset = int(request.query_params.get('offset', 0))
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, offset)

        events = get_recent_events(
            user_id=user_id,
            viewer_id=viewer_id,
            source=source,
            limit=limit,
            offset=offset,
        )
        serialize_timestamps(events)

        return Response({'events': events, 'sources': get_sources()})


class ActivityDailyCountsView(APIView):
    @extend_schema(
        tags=['Activity'],
        summary='Daily activity counts',
        parameters=[
            OpenApiParameter(name='user_id', type=int, required=False),
            OpenApiParameter(name='date_from', type=str, required=True),
            OpenApiParameter(name='date_to', type=str, required=True),
        ],
    )
    def get(self, request):
        date_from_str = request.query_params.get('date_from')
        date_to_str = request.query_params.get('date_to')

        if not date_from_str or not date_to_str:
            return Response(
                {'error': 'date_from and date_to are required'},
                status=400,
            )

        try:
            date_from = date.fromisoformat(date_from_str)
            date_to = date.fromisoformat(date_to_str)
        except ValueError:
            return Response(
                {'error': 'Invalid date format. Use YYYY-MM-DD.'},
                status=400,
            )

        user_id = request.query_params.get('user_id')
        if user_id:
            user_id = int(user_id)
            viewer_id = request.user.id
        else:
            user_id = request.user.id
            viewer_id = None

        counts = activity_registry.get_daily_counts(
            user_id, date_from, date_to, viewer_id=viewer_id,
        )

        return Response({
            'counts': {day.isoformat(): count for day, count in counts.items()},
        })


class ActivityStatsView(APIView):
    @extend_schema(
        tags=['Activity'],
        summary='Activity statistics per provider',
        parameters=[
            OpenApiParameter(name='user_id', type=int, required=False),
        ],
    )
    def get(self, request):
        user_id = request.query_params.get('user_id')
        if user_id:
            user_id = int(user_id)
            viewer_id = request.user.id
        else:
            user_id = request.user.id
            viewer_id = None

        stats = activity_registry.get_stats(user_id, viewer_id=viewer_id)
        return Response(stats)
