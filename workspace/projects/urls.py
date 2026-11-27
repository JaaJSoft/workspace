from django.urls import path

from .views.actions import ProjectAvailableActionsView
from .views.calendar import TaskCalendarView
from .views.epics import EpicDetailView, EpicListView
from .views.labels import LabelDetailView, LabelListView
from .views.members import MemberDetailView, MemberListView, ProjectUserListView
from .views.projects import (
    ProjectConvertView,
    ProjectDetailView,
    ProjectListView,
    ProjectNotificationLevelView,
)
from .views.sprints import (
    SprintCompleteView,
    SprintDetailView,
    SprintListView,
    SprintStartView,
)
from .views.statuses import StatusDetailView, StatusListView, StatusReorderView
from .views.subtasks import SubtaskDetailView, SubtaskListView, SubtaskReorderView
from .views.tasks import (
    AllTasksView,
    TaskBatchView,
    TaskDetailView,
    TaskListView,
    TaskReorderView,
    TaskWatchView,
)
from .views.viewsets import (
    TaskAttachmentViewSet,
    TaskCommentViewSet,
    TaskFileLinkViewSet,
    TaskLinkViewSet,
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
task_file_links = TaskFileLinkViewSet.as_view({"get": "list", "post": "create"})
task_file_link_detail = TaskFileLinkViewSet.as_view({"delete": "destroy"})

urlpatterns = [
    path("api/v1/projects", ProjectListView.as_view(), name="project-list"),
    path(
        "api/v1/projects/available-actions",
        ProjectAvailableActionsView.as_view(),
        name="project-available-actions",
    ),
    path("api/v1/projects/tasks", AllTasksView.as_view(), name="project-all-tasks"),
    path(
        "api/v1/projects/tasks/calendar",
        TaskCalendarView.as_view(),
        name="project-tasks-calendar",
    ),
    path(
        "api/v1/projects/tasks/<uuid:uuid>",
        TaskDetailView.as_view(),
        name="project-task-detail",
    ),
    path(
        "api/v1/projects/tasks/<uuid:uuid>/watch",
        TaskWatchView.as_view(),
        name="project-task-watch",
    ),
    path(
        "api/v1/projects/tasks/<uuid:task_uuid>/subtasks",
        SubtaskListView.as_view(),
        name="project-task-subtasks",
    ),
    path(
        "api/v1/projects/tasks/<uuid:task_uuid>/subtasks/reorder",
        SubtaskReorderView.as_view(),
        name="project-task-subtasks-reorder",
    ),
    path(
        "api/v1/projects/subtasks/<uuid:uuid>",
        SubtaskDetailView.as_view(),
        name="project-subtask-detail",
    ),
    path(
        "api/v1/projects/members/<uuid:uuid>",
        MemberDetailView.as_view(),
        name="project-member-detail",
    ),
    path(
        "api/v1/projects/labels/<uuid:uuid>",
        LabelDetailView.as_view(),
        name="project-label-detail",
    ),
    path(
        "api/v1/projects/epics/<uuid:uuid>",
        EpicDetailView.as_view(),
        name="project-epic-detail",
    ),
    path(
        "api/v1/projects/sprints/<uuid:uuid>",
        SprintDetailView.as_view(),
        name="project-sprint-detail",
    ),
    path(
        "api/v1/projects/sprints/<uuid:uuid>/start",
        SprintStartView.as_view(),
        name="project-sprint-start",
    ),
    path(
        "api/v1/projects/sprints/<uuid:uuid>/complete",
        SprintCompleteView.as_view(),
        name="project-sprint-complete",
    ),
    path(
        "api/v1/projects/statuses/<uuid:uuid>",
        StatusDetailView.as_view(),
        name="project-status-detail",
    ),
    path(
        "api/v1/projects/<uuid:uuid>",
        ProjectDetailView.as_view(),
        name="project-detail",
    ),
    path(
        "api/v1/projects/<uuid:uuid>/convert",
        ProjectConvertView.as_view(),
        name="project-convert",
    ),
    path(
        "api/v1/projects/<uuid:uuid>/notification-level",
        ProjectNotificationLevelView.as_view(),
        name="project-notification-level",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/members",
        MemberListView.as_view(),
        name="project-members",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/users",
        ProjectUserListView.as_view(),
        name="project-users",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/labels",
        LabelListView.as_view(),
        name="project-labels",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/epics",
        EpicListView.as_view(),
        name="project-epics",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/sprints",
        SprintListView.as_view(),
        name="project-sprints",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/statuses",
        StatusListView.as_view(),
        name="project-statuses",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/statuses/reorder",
        StatusReorderView.as_view(),
        name="project-statuses-reorder",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks",
        TaskListView.as_view(),
        name="project-tasks",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/batch",
        TaskBatchView.as_view(),
        name="project-tasks-batch",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/reorder",
        TaskReorderView.as_view(),
        name="project-tasks-reorder",
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
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/files",
        task_file_links,
        name="project-task-file-links",
    ),
    path(
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>/files/<uuid:uuid>",
        task_file_link_detail,
        name="project-task-file-link-detail",
    ),
]
