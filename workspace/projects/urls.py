from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .views_actions import ProjectActionsView
from .viewsets import (
    LabelViewSet,
    MemberViewSet,
    ProjectViewSet,
    StatusViewSet,
    TaskViewSet,
)

router = SimpleRouter(trailing_slash=False)
router.register(r"projects", ProjectViewSet, basename="project")

member_list = MemberViewSet.as_view({"get": "list", "post": "create"})
member_detail = MemberViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
label_list = LabelViewSet.as_view({"get": "list", "post": "create"})
label_detail = LabelViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
status_list = StatusViewSet.as_view({"get": "list"})
task_list = TaskViewSet.as_view({"get": "list", "post": "create"})
task_reorder = TaskViewSet.as_view({"post": "reorder"})
task_detail = TaskViewSet.as_view(
    {"get": "retrieve", "patch": "partial_update", "delete": "destroy"}
)

urlpatterns = [
    path(
        "api/v1/projects/actions",
        ProjectActionsView.as_view(),
        name="project-actions",
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
        "api/v1/projects/<uuid:project_uuid>/tasks/<uuid:task_uuid>",
        task_detail,
        name="project-task-detail",
    ),
    path("api/v1/", include(router.urls)),
]
