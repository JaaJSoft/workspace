import re

from django.test import TestCase

from workspace.projects.services.tasks import create_task
from workspace.users.services.settings import set_setting

from .base import ProjectTestMixin
from .test_ui import SettingsCleanupMixin

FRENCH_BY_URGENCY = ["Urgente", "Haute", "Moyenne", "Basse"]


def _menu_choices(html, marker):
    """The labelled buttons whose click handler contains ``marker``, in order."""
    rows = re.findall(r'<button[^>]*@click="([^"]*)"[^>]*>(.*?)</button>', html, re.S)
    texts = [
        re.sub(r"<[^>]+>", "", text).strip()
        for handler, text in rows
        if marker in handler
    ]
    return [text for text in texts if text]


class PriorityMenusTests(SettingsCleanupMixin, ProjectTestMixin, TestCase):
    """Every priority picker offers the four priorities, most urgent first."""

    def setUp(self):
        super().setUp()
        self.task = create_task(self.project, self.admin, title="Ship it")
        set_setting(self.admin, "core", "language", "fr")
        self.client.force_login(self.admin)

    def test_task_panel(self):
        html = self.client.get(
            f"/projects/{self.project.uuid}/tasks/{self.task.uuid}/panel"
        ).content.decode()
        self.assertEqual(_menu_choices(html, "data.priority = "), FRENCH_BY_URGENCY)

    def test_filters_and_task_form(self):
        html = self.client.get(
            f"/projects/{self.project.uuid}/backlog"
        ).content.decode()
        self.assertEqual(
            _menu_choices(html, "filters.priority = '"),
            ["Toutes les priorités", *FRENCH_BY_URGENCY],
        )
        self.assertEqual(_menu_choices(html, "form.priority = "), FRENCH_BY_URGENCY)

    def test_bulk_toolbar(self):
        html = self.client.get(
            f"/projects/{self.project.uuid}/backlog"
        ).content.decode()
        self.assertEqual(_menu_choices(html, "bulkPriority("), FRENCH_BY_URGENCY)
