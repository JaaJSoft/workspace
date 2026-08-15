import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import AgentGoalCreateSerializer, AgentGoalSerializer
from .services.conversations import get_active_membership

logger = logging.getLogger(__name__)


def _get_bot_member(conversation_id):
    """Return the first bot User active in the conversation, or None."""
    from .models import ConversationMember

    member = (
        ConversationMember.objects.filter(
            conversation_id=conversation_id,
            left_at__isnull=True,
            user__bot_profile__isnull=False,
        )
        .select_related("user")
        .first()
    )
    return member.user if member else None


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

    @extend_schema(
        summary="Create an agent goal for the conversation's bot",
        request=AgentGoalCreateSerializer,
        responses=AgentGoalSerializer,
    )
    def post(self, request, conversation_id):
        from django.utils import timezone

        from workspace.ai.models import AgentGoal

        membership = get_active_membership(request.user, conversation_id)
        if not membership:
            return Response(
                {"detail": "Not a member of this conversation."},
                status=status.HTTP_403_FORBIDDEN,
            )

        bot_user = _get_bot_member(conversation_id)
        if bot_user is None:
            return Response(
                {"detail": "This conversation has no AI bot."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = AgentGoalCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        active_count = AgentGoal.objects.filter(
            conversation_id=conversation_id,
            bot=bot_user,
            status=AgentGoal.Status.ACTIVE,
        ).count()
        if active_count >= AgentGoal.MAX_ACTIVE_PER_CONVERSATION:
            return Response(
                {"detail": "Too many active goals in this conversation."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        goal_text = data["goal"].strip()
        if not goal_text:
            return Response(
                {"detail": "Goal is required."}, status=status.HTTP_400_BAD_REQUEST
            )
        title = (data.get("title") or "").strip() or goal_text[:200]

        first_check = data.get("first_check_at") or (
            timezone.now() + AgentGoal.MIN_CHECK_INTERVAL
        )
        goal = AgentGoal.objects.create(
            conversation_id=conversation_id,
            bot=bot_user,
            created_by=request.user,
            title=title[:200],
            goal=goal_text,
            success_criteria=(data.get("success_criteria") or "").strip(),
            constraints=(data.get("constraints") or "").strip(),
            reporting=(data.get("reporting") or "").strip(),
            deadline=data.get("deadline"),
            next_check_at=AgentGoal.clamp_next_check(first_check),
        )
        return Response(AgentGoalSerializer(goal).data, status=status.HTTP_201_CREATED)


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

    @extend_schema(
        summary="Update an agent goal (mission brief, notes, schedule, pause/resume)"
    )
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
