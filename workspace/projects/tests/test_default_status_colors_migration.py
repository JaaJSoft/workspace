from importlib import import_module

from django.apps import apps
from django.test import TestCase

from workspace.projects.models import TaskStatus

from .base import ProjectTestMixin

colorize = import_module(
    "workspace.projects.migrations.0021_default_status_colors"
).colorize


class DefaultStatusColorsMigrationTests(ProjectTestMixin, TestCase):
    """The backfill runs on the final schema, so it is testable by clearing
    colors to simulate the legacy uncolored state."""

    def _simulate_legacy_state(self):
        TaskStatus.objects.update(color="")

    def test_colors_matching_default_columns(self):
        self._simulate_legacy_state()

        colorize(apps, None)

        colors = list(
            self.project.statuses.order_by("position").values_list("color", flat=True)
        )
        self.assertEqual(colors, ["#a855f7", "#3b82f6", "#eab308", "#22c55e"])

    def test_leaves_renamed_and_colored_columns_alone(self):
        self._simulate_legacy_state()
        renamed = self.project.statuses.get(name="Backlog")
        renamed.name = "Icebox"
        renamed.save(update_fields=["name"])
        colored = self.project.statuses.get(name="Done")
        colored.color = "#ec4899"
        colored.save(update_fields=["color"])

        colorize(apps, None)

        renamed.refresh_from_db()
        colored.refresh_from_db()
        self.assertEqual(renamed.color, "")
        self.assertEqual(colored.color, "#ec4899")
