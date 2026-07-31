from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from workspace.projects.models import Project, Task, TaskEvent
from workspace.projects.services.projects import get_or_create_personal_project
from workspace.projects.services.tasks import create_task, delete_task
from workspace.users.services.settings import get_setting, set_setting

from .base import ProjectTestMixin


class SettingsCleanupMixin:
    def tearDown(self):
        cache.clear()
        super().tearDown()


class IndexRedirectTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_no_setting_creates_personal_project_and_redirects(self):
        self.client.force_login(self.member)
        response = self.client.get("/projects")
        personal = Project.objects.get(
            created_by=self.member, type=Project.Type.PERSONAL
        )
        self.assertRedirects(response, f"/projects/{personal.uuid}")

    def test_redirects_to_last_opened_project(self):
        set_setting(self.member, "projects", "last_project", str(self.project.uuid))
        self.client.force_login(self.member)
        response = self.client.get("/projects")
        self.assertRedirects(response, f"/projects/{self.project.uuid}")

    def test_inaccessible_last_project_falls_back_to_personal(self):
        set_setting(self.outsider, "projects", "last_project", str(self.project.uuid))
        self.client.force_login(self.outsider)
        response = self.client.get("/projects")
        personal = Project.objects.get(
            created_by=self.outsider, type=Project.Type.PERSONAL
        )
        self.assertRedirects(response, f"/projects/{personal.uuid}")

    def test_malformed_last_project_falls_back_to_personal(self):
        set_setting(self.member, "projects", "last_project", "not-a-uuid")
        self.client.force_login(self.member)
        response = self.client.get("/projects")
        personal = Project.objects.get(
            created_by=self.member, type=Project.Type.PERSONAL
        )
        self.assertRedirects(response, f"/projects/{personal.uuid}")

    def test_anonymous_redirected_to_login(self):
        response = self.client.get("/projects")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])


class OverviewViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_renders_overview_with_task_counts(self):
        todo_status = self.project.statuses.get(name="To do")
        backlog_status = self.project.statuses.get(name="Backlog")
        done_status = self.project.statuses.get(name="Done")
        create_task(self.project, self.admin, title="Active", status=todo_status)
        create_task(self.project, self.admin, title="Queued", status=backlog_status)
        create_task(self.project, self.admin, title="Shipped", status=done_status)
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertEqual(response.context["view"], "overview")
        self.assertEqual(response.context["board_count"], 1)
        self.assertEqual(response.context["backlog_count"], 1)
        self.assertEqual(response.context["done_count"], 1)
        self.assertContains(response, "Members")
        self.assertContains(response, "member1")
        self.assertContains(response, "admin1")

    def test_records_last_project(self):
        self.client.force_login(self.member)
        self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(
            get_setting(self.member, "projects", "last_project"),
            str(self.project.uuid),
        )

    def test_partial_returns_content_wrapper(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertTemplateNotUsed(response, "projects/ui/project.html")
        self.assertContains(response, 'id="project-content"')
        self.assertContains(response, 'id="overview"')

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(response.status_code, 404)


class BoardViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_renders_board_columns_without_backlog_column(self):
        todo_status = self.project.statuses.get(name="To do")
        create_task(
            self.project, self.admin, title="Fix the login flow", status=todo_status
        )
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertContains(response, "To do")
        self.assertContains(response, "In progress")
        self.assertContains(response, "Done")
        self.assertContains(response, "Fix the login flow")
        column_names = [c["status"].name for c in response.context["columns"]]
        self.assertNotIn("Backlog", column_names)
        self.assertEqual(response.context["view"], "board")

    def test_records_last_project(self):
        self.client.force_login(self.member)
        self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertEqual(
            get_setting(self.member, "projects", "last_project"),
            str(self.project.uuid),
        )

    def test_partial_returns_content_wrapper(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}/board", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertTemplateNotUsed(response, "projects/ui/project.html")
        self.assertContains(response, 'id="project-content"')

    def test_board_has_shared_filter_bar(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertContains(response, 'id="task-filters"')
        self.assertContains(response, 'x-model="filters.q"')

    def test_members_data_exposes_user_ids(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertIn(
            {
                "id": str(self.member.pk),
                "username": "member1",
                "first_name": "",
                "last_name": "",
            },
            response.context["members_data"],
        )

    def test_members_data_includes_group_users(self):
        group = Group.objects.create(name="devs")
        grouper = get_user_model().objects.create_user(
            username="grouper1", email="grouper1@test.com", password="pass123"
        )
        grouper.groups.add(group)
        self.project.groups.add(group)
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        ids = [m["id"] for m in response.context["members_data"]]
        self.assertIn(str(grouper.pk), ids)

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertEqual(response.status_code, 404)

    def test_board_cards_show_the_task_reference(self):
        task = create_task(
            self.project,
            self.admin,
            title="Referenced",
            status=self.project.statuses.get(name="To do"),
        )
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertContains(resp, f"{self.project.key}-{task.number}")


class BoardDoneRetentionTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.done = self.project.statuses.get(name="Done")

    def _set_retention(self, days):
        self.project.done_retention_days = days
        self.project.save(update_fields=["done_retention_days"])

    def _make_done_task(self, title, completed_days_ago):
        task = create_task(self.project, self.admin, title=title, status=self.done)
        Task.objects.filter(pk=task.pk).update(
            completed_at=timezone.now() - timedelta(days=completed_days_ago)
        )
        return task

    def _board(self):
        self.client.force_login(self.member)
        return self.client.get(f"/projects/{self.project.uuid}/board")

    def test_all_done_tasks_visible_without_retention(self):
        self._make_done_task("Ancient win", 400)
        self.assertContains(self._board(), "Ancient win")

    def test_done_task_older_than_retention_is_hidden(self):
        self._set_retention(7)
        self._make_done_task("Old news", 8)
        self.assertNotContains(self._board(), "Old news")

    def test_recent_done_task_stays_visible(self):
        self._set_retention(7)
        self._make_done_task("Fresh win", 2)
        self.assertContains(self._board(), "Fresh win")

    def test_done_task_without_completed_at_stays_visible(self):
        self._set_retention(7)
        task = create_task(
            self.project, self.admin, title="No timestamp", status=self.done
        )
        Task.objects.filter(pk=task.pk).update(completed_at=None)
        self.assertContains(self._board(), "No timestamp")

    def test_active_tasks_unaffected_by_retention(self):
        self._set_retention(1)
        todo = self.project.statuses.get(name="To do")
        create_task(self.project, self.admin, title="Still doing", status=todo)
        self.assertContains(self._board(), "Still doing")

    def test_hidden_count_shown_in_done_column(self):
        self._set_retention(7)
        self._make_done_task("Old one", 10)
        self._make_done_task("Old two", 20)
        self._make_done_task("Recent", 1)
        response = self._board()
        done_column = next(
            c for c in response.context["columns"] if c["status"].pk == self.done.pk
        )
        self.assertEqual(done_column["hidden_count"], 2)
        self.assertContains(response, "2 hidden")

    def test_no_hidden_counter_when_nothing_is_hidden(self):
        self._set_retention(7)
        self._make_done_task("Recent", 1)
        response = self._board()
        done_column = next(
            c for c in response.context["columns"] if c["status"].pk == self.done.pk
        )
        self.assertEqual(done_column["hidden_count"], 0)
        self.assertNotContains(response, 'data-lucide="eye-off"')


class BacklogViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_renders_backlog(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertEqual(response.context["view"], "backlog")

    def test_backlog_has_filter_bar_and_bulk_toolbar(self):
        create_task(self.project, self.admin, title="Queued work")
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertContains(response, 'id="task-filters"')
        self.assertContains(response, "Send to board")
        self.assertContains(response, 'x-model="filters.q"')
        self.assertContains(response, "toggleSelectAll()")

    def test_backlog_rows_expose_filter_metadata(self):
        label = self.project.labels.create(name="Bug", color="#ff0000")
        task = create_task(
            self.project,
            self.admin,
            title="Fix Login",
            assignees=[self.member],
            labels=[label],
        )
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertContains(response, f'data-task-uuid="{task.uuid}"')
        self.assertContains(response, 'data-priority="medium"')
        self.assertContains(response, "fix login bug")
        self.assertContains(response, f'data-assignees="{self.member.pk} "')
        self.assertContains(response, f'data-labels="{label.uuid} "')

    def test_backlog_filter_options_come_from_project_data(self):
        self.project.labels.create(name="Bug", color="#ff0000")
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertContains(
            response, f'<option value="{self.member.pk}">member1</option>'
        )
        self.assertContains(response, "All labels")
        self.assertContains(response, "Unassigned")

    def test_archived_project_backlog_has_no_bulk_controls(self):
        from django.utils import timezone

        create_task(self.project, self.admin, title="Queued work")
        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertNotContains(response, "Send to board")
        self.assertNotContains(response, "toggleSelectAll()")
        self.assertContains(response, 'id="task-filters"')

    def test_partial_returns_content_wrapper(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}/backlog", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertContains(response, 'id="project-content"')

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertEqual(response.status_code, 404)


class OverviewActivityTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_overview_shows_recent_events(self):
        create_task(self.project, self.admin, title="Paint the shed")
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Recent activity")
        self.assertContains(resp, "Task created")
        self.assertContains(resp, "Paint the shed")

    def test_overview_empty_activity_state(self):
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}")
        self.assertContains(resp, "No activity yet.")

    def test_overview_activity_links_to_task(self):
        task = create_task(self.project, self.admin, title="Paint the shed")
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}")
        self.assertContains(resp, f"openTask('{task.uuid}')")

    def test_overview_activity_deleted_task_is_not_clickable(self):
        task = create_task(self.project, self.admin, title="Paint the shed")
        task_uuid = task.uuid
        task.delete()
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}")
        self.assertContains(resp, "Paint the shed")
        self.assertNotContains(resp, f"openTask('{task_uuid}')")

    def test_overview_activity_deleted_task_still_shows_reference(self):
        task = create_task(self.project, self.admin, title="Paint the shed")
        task_number = task.number
        delete_task(task, actor=self.admin)
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}")
        self.assertContains(resp, f"{self.project.key}-{task_number}")

    def test_overview_activity_shows_status_transition(self):
        task = create_task(self.project, self.admin, title="Paint the shed")
        TaskEvent.objects.create(
            project=self.project,
            task=task,
            task_title=task.title,
            actor=self.admin,
            type=TaskEvent.Type.MOVED,
            from_status="Backlog",
            to_status="In progress",
        )
        self.client.force_login(self.admin)
        resp = self.client.get(f"/projects/{self.project.uuid}")
        self.assertContains(resp, "Backlog &rarr; In progress")


class SidebarTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_switcher_lists_only_accessible_projects(self):
        personal = get_or_create_personal_project(self.member)
        other = Project.objects.create(
            name="Admin only", created_by=self.admin, type=Project.Type.KANBAN
        )
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertContains(response, "Website")
        self.assertContains(response, personal.name)
        self.assertNotContains(response, "Admin only")
        sidebar_uuids = [p.uuid for p in response.context["projects"]]
        self.assertNotIn(other.uuid, sidebar_uuids)
        self.assertEqual(sidebar_uuids[0], personal.uuid)

    def test_sidebar_links_to_project_views(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertContains(response, f"/projects/{self.project.uuid}/backlog")
        self.assertContains(response, f"/projects/{self.project.uuid}/board")

    def test_partial_response_has_no_sidebar(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}/board", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertNotContains(response, "drawer-side")
        self.assertNotIn("projects", response.context)


class SettingsViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def test_admin_gets_settings_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertEqual(response.context["view"], "settings")

    def test_project_data_embeds_done_retention(self):
        self.project.done_retention_days = 14
        self.project.save(update_fields=["done_retention_days"])
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertEqual(response.context["project_data"]["done_retention_days"], 14)

    def test_member_gets_404(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertEqual(response.status_code, 404)

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertEqual(response.status_code, 404)

    def test_partial_returns_content_wrapper(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            f"/projects/{self.project.uuid}/settings", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertContains(response, 'id="project-content"')

    def test_columns_data_includes_task_counts(self):
        todo = self.project.statuses.get(name="To do")
        create_task(self.project, self.admin, title="A", status=todo)
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        by_name = {c["name"]: c for c in response.context["columns_data"]}
        self.assertEqual(by_name["To do"]["task_count"], 1)
        self.assertEqual(by_name["Done"]["task_count"], 0)

    def test_sidebar_shows_settings_entry_for_admin_only(self):
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertContains(response, f"/projects/{self.project.uuid}/settings")
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertNotContains(response, f"/projects/{self.project.uuid}/settings")

    def test_archived_project_settings_reachable(self):
        from django.utils import timezone

        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertEqual(response.status_code, 200)

    def test_settings_page_has_general_and_danger_sections(self):
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertContains(response, 'id="settings-general"')
        self.assertContains(response, 'id="settings-danger"')
        self.assertContains(response, "writable: true")

    def test_personal_project_hides_danger_zone_and_group(self):
        personal = get_or_create_personal_project(self.admin)
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{personal.uuid}/settings")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="settings-danger"')
        self.assertNotContains(response, 'id="settings-group"')

    def test_archived_project_passes_writable_false_to_general(self):
        from django.utils import timezone

        self.project.archived_at = timezone.now()
        self.project.save(update_fields=["archived_at"])
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertContains(response, "writable: false")

    def test_settings_context_exposes_attached_groups(self):
        devs = Group.objects.create(name="devs")
        design = Group.objects.create(name="design")
        self.project.groups.add(devs, design)
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertEqual(
            response.context["project_data"]["groups"],
            [
                {"id": design.pk, "name": "design"},
                {"id": devs.pk, "name": "devs"},
            ],
        )

    def test_settings_page_has_columns_section(self):
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertContains(response, 'id="settings-columns"')

    def test_settings_page_has_labels_and_members_sections(self):
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/settings")
        self.assertContains(response, 'id="settings-labels"')
        self.assertContains(response, 'id="settings-members"')
        self.assertContains(response, 'id="settings-group"')

    def test_personal_project_hides_members_section(self):
        personal = get_or_create_personal_project(self.admin)
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{personal.uuid}/settings")
        self.assertContains(response, 'id="settings-labels"')
        self.assertNotContains(response, 'id="settings-members"')


class AllTasksViewTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    def _seed_one_task_per_category(self):
        backlog = self.project.statuses.get(name="Backlog")
        todo = self.project.statuses.get(name="To do")
        done = self.project.statuses.get(name="Done")
        create_task(self.project, self.admin, title="Queued work", status=backlog)
        create_task(self.project, self.admin, title="Active work", status=todo)
        create_task(self.project, self.admin, title="Shipped work", status=done)

    def test_renders_tasks_from_all_categories(self):
        self._seed_one_task_per_category()
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "projects/ui/project.html")
        self.assertEqual(response.context["view"], "tasks")
        self.assertEqual(len(response.context["all_tasks"]), 3)
        self.assertContains(response, "Queued work")
        self.assertContains(response, "Active work")
        self.assertContains(response, "Shipped work")

    def test_tasks_follow_status_position_order(self):
        self._seed_one_task_per_category()
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/tasks")
        html = response.content.decode()
        self.assertLess(html.index("Queued work"), html.index("Active work"))
        self.assertLess(html.index("Active work"), html.index("Shipped work"))

    def test_rows_are_readonly_with_status_metadata(self):
        self._seed_one_task_per_category()
        todo = self.project.statuses.get(name="To do")
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/tasks")
        self.assertNotContains(response, "Select task")
        self.assertNotContains(response, "Send to board")
        self.assertNotContains(response, 'draggable="true"')
        self.assertContains(response, f'data-status="{todo.uuid}"')

    def test_partial_returns_content_wrapper(self):
        self.client.force_login(self.member)
        response = self.client.get(
            f"/projects/{self.project.uuid}/tasks", HTTP_X_ALPINE_REQUEST="1"
        )
        self.assertTemplateUsed(response, "projects/ui/partials/_content.html")
        self.assertTemplateNotUsed(response, "projects/ui/project.html")
        self.assertContains(response, 'id="all-tasks"')

    def test_empty_project_shows_empty_state(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/tasks")
        self.assertContains(response, "This project has no tasks yet.")

    def test_outsider_gets_404(self):
        self.client.force_login(self.outsider)
        response = self.client.get(f"/projects/{self.project.uuid}/tasks")
        self.assertEqual(response.status_code, 404)

    def test_sidebar_links_to_all_tasks(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}")
        self.assertContains(response, f"/projects/{self.project.uuid}/tasks")
        self.assertContains(response, "All tasks")

    def test_backlog_rows_keep_bulk_controls(self):
        # Regression: the readonly flags must not leak into the backlog tab.
        backlog = self.project.statuses.get(name="Backlog")
        create_task(self.project, self.admin, title="Queued work", status=backlog)
        self.client.force_login(self.admin)
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertContains(response, "Select task")
        self.assertContains(response, 'draggable="true"')

    def test_status_filter_renders_only_on_all_tasks_view(self):
        self.client.force_login(self.member)
        response = self.client.get(f"/projects/{self.project.uuid}/tasks")
        self.assertContains(response, 'aria-label="Filter by status"')
        self.assertContains(response, "All statuses")
        for status in self.project.statuses.all():
            self.assertContains(response, f'<option value="{status.uuid}">')
        response = self.client.get(f"/projects/{self.project.uuid}/backlog")
        self.assertNotContains(response, 'aria-label="Filter by status"')
        response = self.client.get(f"/projects/{self.project.uuid}/board")
        self.assertNotContains(response, 'aria-label="Filter by status"')


class BoardLabelSelectorTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    """Task modal label combobox: admins get inline create, members only pick."""

    def board(self):
        return self.client.get(f"/projects/{self.project.uuid}/board")

    def test_admin_gets_the_create_endpoint(self):
        self.client.force_login(self.admin)
        resp = self.board()
        self.assertContains(resp, "labelSelector(")
        self.assertContains(resp, f"/api/v1/projects/{self.project.uuid}/labels")

    def test_member_gets_the_selector_without_the_create_endpoint(self):
        self.client.force_login(self.member)
        resp = self.board()
        self.assertContains(resp, "labelSelector(")
        self.assertNotContains(resp, f"/api/v1/projects/{self.project.uuid}/labels")

    def test_member_modal_section_collapses_without_labels(self):
        # Members cannot create labels, so an empty project hides the section
        # client-side; admins always see it (they can create the first label).
        self.client.force_login(self.member)
        self.assertContains(self.board(), 'x-show="labels.length"')
        self.client.force_login(self.admin)
        self.assertNotContains(self.board(), 'x-show="labels.length"')
