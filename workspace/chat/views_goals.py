import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import AgentGoalSerializer
from .services.conversations import get_active_membership

logger = logging.getLogger(__name__)


@extend_schema(tags=["Chat"])
class AgentGoalListView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="List open agent goals for a conversation")
    def get(self, request, conversation_id):
        from workspace.ai.models import AgentGoal

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {"detail": "Not a member of this conversation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        goals = (
            AgentGoal.objects.filter(
                conversation_id=conversation_id,
                status__in=[AgentGoal.Status.ACTIVE, AgentGoal.Status.PAUSED],
            )
            .select_related("bot")
            .order_by("next_check_at")
        )
        serializer = AgentGoalSerializer(goals, many=True)
        return Response(serializer.data)


@extend_schema(tags=["Chat"])
class AgentGoalDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_goal(self, conversation_id, goal_id):
        from workspace.ai.models import AgentGoal

        return (
            AgentGoal.objects.filter(
                uuid=goal_id,
                conversation_id=conversation_id,
                status__in=[AgentGoal.Status.ACTIVE, AgentGoal.Status.PAUSED],
            )
            .select_related("bot")
            .first()
        )

    @extend_schema(summary="Update an agent goal (title, objective, pause/resume)")
    def patch(self, request, conversation_id, goal_id):
        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {"detail": "Not a member of this conversation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        goal = self._get_goal(conversation_id, goal_id)
        if not goal:
            return Response(
                {"detail": "Agent goal not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = AgentGoalSerializer(goal, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = serializer.save()
        return Response(AgentGoalSerializer(updated).data)

    @extend_schema(summary="Stop an agent goal (marks it abandoned)")
    def delete(self, request, conversation_id, goal_id):
        from workspace.ai.models import AgentGoal

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {"detail": "Not a member of this conversation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        goal = self._get_goal(conversation_id, goal_id)
        if not goal:
            return Response(
                {"detail": "Agent goal not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        goal.status = AgentGoal.Status.ABANDONED
        goal.outcome = goal.outcome or "Stopped by the user."
        goal.save(update_fields=["status", "outcome", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
