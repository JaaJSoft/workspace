from django.urls import include, path
from rest_framework.routers import SimpleRouter

from .viewsets import LabelViewSet, MemberViewSet, ProjectViewSet, StatusViewSet

router = SimpleRouter(trailing_slash=False)
router.register(r"projects", ProjectViewSet, basename="project")

member_list = MemberViewSet.as_view({"get": "list", "post": "create"})
member_detail = MemberViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
label_list = LabelViewSet.as_view({"get": "list", "post": "create"})
label_detail = LabelViewSet.as_view({"patch": "partial_update", "delete": "destroy"})
status_list = StatusViewSet.as_view({"get": "list"})

urlpatterns = [
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
    path("api/v1/", include(router.urls)),
]
