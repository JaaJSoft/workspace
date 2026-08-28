from unittest.mock import patch

from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.projects.models import Project
from workspace.projects.serializers import ProjectSerializer
from workspace.projects.services.projects import (
    create_project,
    get_or_create_personal_project,
)

from .base import ProjectTestMixin


class ProjectListCreateTests(ProjectTestMixin, APITestCase):
    def test_list_shows_my_projects_with_role(self):
        self.client.force_authenticate(self.member)
        response = self.client.get("/api/v1/projects")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "Website")
        self.assertEqual(response.data[0]["my_role"], "member")

    def test_list_excludes_other_projects(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get("/api/v1/projects")
        self.assertEqual(response.data, [])

    def test_create_seeds_statuses_and_admin(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            "/api/v1/projects", {"name": "New", "description": "d"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["my_role"], "admin")
        project = Project.objects.get(uuid=response.data["uuid"])
        self.assertEqual(project.statuses.count(), 4)
        self.assertEqual(project.type, Project.Type.KANBAN)

    def test_create_rejects_group_user_is_not_in(self):
        group = Group.objects.create(name="devs")
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            "/api/v1/projects",
            {"name": "New", "groups": [group.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_with_groups_attaches_them(self):
        devs = Group.objects.create(name="devs")
        design = Group.objects.create(name="design")
        self.outsider.groups.add(devs, design)
        self.client.force_authenticate(self.outsider)
        response = self.client.post(
            "/api/v1/projects",
            {"name": "New", "groups": [devs.pk, design.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(set(response.data["groups"]), {devs.pk, design.pk})
        project = Project.objects.get(uuid=response.data["uuid"])
        self.assertEqual(
            set(project.groups.values_list("pk", flat=True)), {devs.pk, design.pk}
        )

    def test_group_only_access_lists_project_as_member(self):
        group = Group.objects.create(name="devs")
        self.outsider.groups.add(group)
        self.project.groups.add(group)
        self.client.force_authenticate(self.outsider)
        response = self.client.get("/api/v1/projects")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["my_role"], "member")


class ProjectDetailTests(ProjectTestMixin, APITestCase):
    def test_outsider_gets_404(self):
        self.client.force_authenticate(self.outsider)
        response = self.client.get(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_member_cannot_rename(self):
        self.client.force_authenticate(self.member)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}", {"name": "X"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_renames(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}", {"name": "X"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertEqual(self.project.name, "X")

    def test_admin_deletes(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Project.objects.filter(uuid=self.project.uuid).exists())

    def test_member_cannot_delete(self):
        self.client.force_authenticate(self.member)
        response = self.client.delete(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_deletes_project_containing_tasks(self):
        from workspace.projects.services.tasks import create_task

        create_task(self.project, self.admin, title="t")
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Project.objects.filter(uuid=self.project.uuid).exists())

    def test_personal_project_cannot_be_deleted_or_archived(self):
        personal = get_or_create_personal_project(self.admin)
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/projects/{personal.uuid}")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(f"/api/v1/projects/{personal.uuid}/archive")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_personal_project_cannot_attach_group(self):
        group = Group.objects.create(name="devs")
        self.admin.groups.add(group)
        personal = get_or_create_personal_project(self.admin)
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{personal.uuid}",
            {"groups": [group.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_rejects_added_group_user_is_not_in(self):
        group = Group.objects.create(name="devs")
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}",
            {"groups": [group.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_keeps_already_attached_foreign_group(self):
        # Attached by another admin: keeping it in the list must not require
        # the requesting admin to belong to it, only newly added groups do.
        foreign = Group.objects.create(name="ops")
        self.project.groups.add(foreign)
        mine = Group.objects.create(name="devs")
        self.admin.groups.add(mine)
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}",
            {"groups": [foreign.pk, mine.pk]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(self.project.groups.values_list("pk", flat=True)),
            {foreign.pk, mine.pk},
        )

    def test_update_can_detach_foreign_group(self):
        foreign = Group.objects.create(name="ops")
        self.project.groups.add(foreign)
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}",
            {"groups": []},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.project.groups.count(), 0)

    def test_archive_and_unarchive(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(f"/api/v1/projects/{self.project.uuid}/archive")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_archived)
        response = self.client.post(f"/api/v1/projects/{self.project.uuid}/unarchive")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_archived)

    def test_rename_blocked_while_archived(self):
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/projects/{self.project.uuid}", {"name": "X"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class ProjectDoneRetentionApiTests(ProjectTestMixin, APITestCase):
    def _patch(self, payload):
        self.client.force_authenticate(self.admin)
        return self.client.patch(
            f"/api/v1/projects/{self.project.uuid}", payload, format="json"
        )

    def test_defaults_to_always_visible(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIsNone(resp.data["done_retention_days"])

    def test_admin_sets_retention(self):
        resp = self._patch({"done_retention_days": 30})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["done_retention_days"], 30)
        self.project.refresh_from_db()
        self.assertEqual(self.project.done_retention_days, 30)

    def test_null_resets_to_always_visible(self):
        self.project.done_retention_days = 14
        self.project.save(update_fields=["done_retention_days"])
        resp = self._patch({"done_retention_days": None})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.done_retention_days)

    def test_out_of_range_values_are_rejected(self):
        for bad in (0, -1, 366, "abc"):
            resp = self._patch({"done_retention_days": bad})
            self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST, bad)


class ProjectKeyApiTests(ProjectTestMixin, APITestCase):
    def _patch(self, user, payload):
        self.client.force_authenticate(user)
        return self.client.patch(
            f"/api/v1/projects/{self.project.uuid}", payload, format="json"
        )

    def test_admin_can_change_the_key(self):
        resp = self._patch(self.admin, {"key": "core7"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["key"], "CORE7")
        self.project.refresh_from_db()
        self.assertEqual(self.project.key, "CORE7")

    def test_invalid_formats_are_rejected(self):
        for bad in ("A", "1AB", "TOOLONGKEY1", "BAD-KEY", "É9", ""):
            resp = self._patch(self.admin, {"key": bad})
            self.assertEqual(resp.status_code, 400, bad)

    def test_duplicate_key_is_rejected_case_insensitively(self):
        other = create_project(self.admin, name="Other Board")
        resp = self._patch(self.admin, {"key": other.key.lower()})
        self.assertEqual(resp.status_code, 400)

    def test_member_cannot_change_the_key(self):
        resp = self._patch(self.member, {"key": "NOPE"})
        self.assertEqual(resp.status_code, 403)

    def test_key_on_create_is_ignored(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            "/api/v1/projects", {"name": "Fresh Board", "key": "FORCED"}, format="json"
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["key"], "FB")

    def test_invalid_key_on_create_is_ignored_too(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            "/api/v1/projects",
            {"name": "Fresh Board", "key": "not-a-key"},
            format="json",
        )
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.data["key"], "FB")

    def test_concurrent_key_conflict_returns_400(self):
        """Simulates two PATCHes racing past validate_key's exists() check:
        the serializer says the key is free, but the database disagrees by
        the time the row is written, so the unique constraint fires."""
        other = create_project(self.admin, name="Other Board")
        with patch.object(
            ProjectSerializer, "validate_key", lambda self, value: value.strip().upper()
        ):
            resp = self._patch(self.admin, {"key": other.key})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.data["key"], ["Another project already uses this key."])


class ProjectEstimateUnitApiTests(ProjectTestMixin, APITestCase):
    def _patch(self, payload):
        self.client.force_authenticate(self.admin)
        return self.client.patch(
            f"/api/v1/projects/{self.project.uuid}", payload, format="json"
        )

    def test_defaults_to_disabled(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get(f"/api/v1/projects/{self.project.uuid}")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["estimate_unit"], "")

    def test_admin_picks_a_unit(self):
        resp = self._patch({"estimate_unit": "points"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertEqual(self.project.estimate_unit, "points")

    def test_blank_disables_estimation(self):
        self.project.estimate_unit = "hours"
        self.project.save(update_fields=["estimate_unit"])
        resp = self._patch({"estimate_unit": ""})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.project.refresh_from_db()
        self.assertEqual(self.project.estimate_unit, "")

    def test_unknown_unit_is_rejected(self):
        resp = self._patch({"estimate_unit": "days"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class ProjectListQueryCountTests(ProjectTestMixin, APITestCase):
    """The listing is unpaginated, so anything resolved per project is
    unbounded work."""

    def _add_projects(self, count):
        # Numbered from the current total so a second call keeps minting
        # unique group names.
        start = Project.objects.count()
        for i in range(start, start + count):
            project = create_project(self.admin, name=f"Scale {i}")
            project.groups.add(Group.objects.create(name=f"scale-group-{i}"))

    def _group_queries(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get("/api/v1/projects")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Access control resolves the caller's own groups too, so count the
        # queries rather than expecting none of them.
        return [q for q in ctx.captured_queries if 'FROM "auth_group"' in q["sql"]]

    def test_groups_are_not_fetched_once_per_project(self):
        self.client.force_authenticate(self.admin)
        self._add_projects(2)
        baseline = len(self._group_queries())

        self._add_projects(10)

        after = len(self._group_queries())
        self.assertEqual(
            after,
            baseline,
            msg=(
                f"the groups M2M must be prefetched - baseline={baseline}, "
                f"after adding 10 projects={after}"
            ),
        )

    def test_query_count_does_not_scale_with_project_count(self):
        self.client.force_authenticate(self.admin)
        self._add_projects(2)

        with CaptureQueriesContext(connection) as ctx_baseline:
            self.client.get("/api/v1/projects")
        baseline = len(ctx_baseline)

        self._add_projects(10)

        with CaptureQueriesContext(connection) as ctx_after:
            response = self.client.get("/api/v1/projects")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            len(ctx_after),
            baseline,
            msg=(
                f"Query count must not scale with project count - "
                f"baseline={baseline}, after adding 10 projects={len(ctx_after)}"
            ),
        )
