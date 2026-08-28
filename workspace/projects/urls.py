from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views.actions import ProjectActionsView
from .views.calendar import TaskCalendarView
from .views.search import TaskSearchView
from .views.viewsets import (
    EpicViewSet,
    LabelViewSet,
    MemberViewSet,
    ProjectNotificationLevelView,
    ProjectViewSet,
    SprintViewSet,
    StatusViewSet,
    SubtaskViewSet,
    TaskAttachmentViewSet,
    TaskCommentViewSet,
    TaskLinkViewSet,
    TaskViewSet,
    TaskWatchView,
)

router = SimpleRouter(trailing_slash=False)
router.register(r"projects", ProjectViewSet, basename="project")

member_list = MemberViewSet.as_view({"get": "list", "post": "create"})
member_detail = MemberViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
label_list = LabelViewSet.as_view({"get": "list", "post": "create"})
label_detail = LabelViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
epic_list = EpicViewSet.as_view({"get": "list", "post": "create"})
epic_detail = EpicViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
sprint_list = SprintViewSet.as_view({"get": "list", "post": "create"})
sprint_detail = SprintViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
sprint_start = SprintViewSet.as_view({"post": "start"})
sprint_complete = SprintViewSet.as_view({"post": "complete"})
status_list = StatusViewSet.as_view({"get": "list", "post": "create"})
status_reorder = StatusViewSet.as_view({"post": "reorder"})
status_detail = StatusViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
task_list = TaskViewSet.as_view({"get": "list", "post": "create"})
task_reorder = TaskViewSet.as_view({"post": "reorder"})
task_move = TaskViewSet.as_view({"post": "move"})
task_assign_sprint = TaskViewSet.as_view({"post": "assign_sprint"})
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
task_attachments = TaskAttachmentViewSet.as_view({"get": "list", "post": "create"})
task_attachment_detail = TaskAttachmentViewSet.as_view({"delete": "destroy"})
task_attachment_download = TaskAttachmentViewSet.as_view({"get": "download"})
task_links = TaskLinkViewSet.as_view({"get": "list", "post": "create"})
task_link_detail = TaskLinkViewSet.as_view({"delete": "destroy"})

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
        "api/v1/projects/tasks/search",
        TaskSearchView.as_view(),
        name="project-tasks-search",
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
        "api/v1/projects/<uuid:project_uuid>/notification-level",
        ProjectNotificationLevelView.as_view(),
        name="project-notification-level",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/watch",
        TaskWatchView.as_view(),
        name="project-task-watch",
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
        "api/v1/projects/<uuid:project_uuid>/epics",
        epic_list,
        name="project-epics",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/epics/<uuid:uuid>",
        epic_detail,
        name="project-epic-detail",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/sprints",
        sprint_list,
        name="project-sprints",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/sprints/<uuid:uuid>",
        sprint_detail,
        name="project-sprint-detail",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/sprints/<uuid:uuid>/start",
        sprint_start,
        name="project-sprint-start",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/sprints/<uuid:uuid>/complete",
        sprint_complete,
        name="project-sprint-complete",
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
        "api/v1/projects/<uuid:project_uuid>/tasks/assign-sprint",
        task_assign_sprint,
        name="project-tasks-assign-sprint",
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
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/attachments",
        task_attachments,
        name="project-task-attachments",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/attachments/<uuid:uuid>",
        task_attachment_detail,
        name="project-task-attachment-detail",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/attachments/<uuid:uuid>/download",
        task_attachment_download,
        name="project-task-attachment-download",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/links",
        task_links,
        name="project-task-links",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/links/<uuid:uuid>",
        task_link_detail,
        name="project-task-link-detail",
    ),
    path("api/v1/", include(router.urls)),
]
