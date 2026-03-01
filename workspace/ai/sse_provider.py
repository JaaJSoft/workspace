from workspace.ai.models import AITask
from workspace.core.sse_registry import SSEProvider


class AISSEProvider(SSEProvider):
    """SSE provider for AI task completion notifications."""

    def get_initial_events(self):
        tasks = AITask.objects.filter(
            owner=self.user,
            status__in=[AITask.Status.PENDING, AITask.Status.PROCESSING],
        ).values('uuid', 'task_type', 'status')

        if not tasks:
            return []

        return [('ai_tasks', {
            'tasks': [
                {'uuid': str(t['uuid']), 'task_type': t['task_type'], 'status': t['status']}
                for t in tasks
            ],
        }, None)]

    def poll(self, cache_value):
        # Only query when notify_sse('ai', user_id) was called
        if cache_value is None:
            return []

        from datetime import timedelta

        from django.utils import timezone

        cutoff = timezone.now() - timedelta(seconds=30)
        tasks = AITask.objects.filter(
            owner=self.user,
            status__in=[AITask.Status.COMPLETED, AITask.Status.FAILED],
            completed_at__gte=cutoff,
        ).values('uuid', 'task_type', 'status', 'result', 'error')

        events = []
        for t in tasks:
            events.append(('ai_task_complete', {
                'uuid': str(t['uuid']),
                'task_type': t['task_type'],
                'status': t['status'],
                'result': t['result'],
                'error': t['error'],
            }, str(t['uuid'])))

        return events
