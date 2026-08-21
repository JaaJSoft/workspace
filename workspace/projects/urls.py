from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views_actions import ProjectActionsView
from .views_calendar import TaskCalendarView
from .viewsets import (
    LabelViewSet,
    MemberViewSet,
    ProjectViewSet,
    StatusViewSet,
    SubtaskViewSet,
    TaskCommentViewSet,
    TaskViewSet,
)

router = SimpleRouter(trailing_slash=False)
router.register(r"projects", ProjectViewSet, basename="project")

member_list = MemberViewSet.as_view({"get": "list", "post": "create"})
member_detail = MemberViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
label_list = LabelViewSet.as_view({"get": "list", "post": "create"})
label_detail = LabelViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
status_list = StatusViewSet.as_view({"get": "list", "post": "create"})
status_reorder = StatusViewSet.as_view({"post": "reorder"})
status_detail = StatusViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
task_list = TaskViewSet.as_view({"get": "list", "post": "create"})
task_reorder = TaskViewSet.as_view({"post": "reorder"})
task_move = TaskViewSet.as_view({"post": "move"})
task_detail = TaskViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)
subtask_list = SubtaskViewSet.as_view({"get": "list", "post": "create"})
subtask_reorder = SubtaskViewSet.as_view({"post": "reorder"})
subtask_detail = SubtaskViewSet.as_view(
    {"patch": "partial_update", "delete": "destroy"}
)
task_comments = TaskCommentViewSet.as_view({"get": "list", "post": "create"})
task_comment_detail = TaskCommentViewSet.as_view(
    {"patch": "partial_update", "delete": "destroy"}
)

urlpatterns = [
    path(
        "api/v1/projects/actions",
        ProjectActionsView.as_view(),
        name="project-actions",
    ),
    path(
        "api/v1/projects/tasks/calendar",
        TaskCalendarView.as_view(),
        name="project-tasks-calendar",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/members",
        member_list,
        name="project-members",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/members/<uuid:uuid>",
        member_detail,
        name="project-member-detail",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/labels",
        label_list,
        name="project-labels",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/labels/<uuid:uuid>",
        label_detail,
        name="project-label-detail",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/statuses",
        status_list,
        name="project-statuses",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/statuses/reorder",
        status_reorder,
        name="project-statuses-reorder",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/statuses/<uuid:uuid>",
        status_detail,
        name="project-status-detail",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks",
        task_list,
        name="project-tasks",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/reorder",
        task_reorder,
        name="project-tasks-reorder",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/move",
        task_move,
        name="project-tasks-move",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>",
        task_detail,
        name="project-task-detail",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/subtasks",
        subtask_list,
        name="project-task-subtasks",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/subtasks/reorder",
        subtask_reorder,
        name="project-task-subtasks-reorder",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/subtasks/<uuid:uuid>",
        subtask_detail,
        name="project-task-subtask-detail",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/comments",
        task_comments,
        name="project-task-comments",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/comments/<uuid:uuid>",
        task_comment_detail,
        name="project-task-comment-detail",
    ),
    path("api/v1/", include(router.urls)),
]
