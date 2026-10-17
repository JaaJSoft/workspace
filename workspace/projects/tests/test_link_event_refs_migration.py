from datetime import timedelta
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.test import TestCase

from workspace.projects.models import TaskEvent
from workspace.projects.services.links import create_link, delete_link
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin

backfill_link_event_refs = import_module(
    "workspace.projects.migrations.0030_backfill_link_event_refs"
).backfill_link_event_refs

_LINK_TYPES = (TaskEvent.Type.LINKED, TaskEvent.Type.UNLINKED)


class _SchemaEditor:
    connection = connection


class BackfillLinkEventRefsTests(ProjectTestMixin, TestCase):
    """The backfill runs on the final schema: blanking the refs simulates
    link history written before they were recorded."""

    def setUp(self):
        super().setUp()
        self.secret = create_project(self.admin, name="Secret")
        self.task = create_task(self.project, self.admin, title="Visible")
        self.hidden = create_task(self.secret, self.admin, title="Hidden")

    def _simulate_legacy_state(self):
        TaskEvent.objects.filter(type__in=_LINK_TYPES).update(
            from_ref=None, to_ref=None
        )

    def _refs(self, task):
        return set(
            task.events.filter(type__in=_LINK_TYPES).values_list("from_ref", "to_ref")
        )

    def test_pairs_both_ends_of_each_link(self):
        link = create_link(self.hidden, self.task, "blocks", actor=self.admin)
        delete_link(link, actor=self.member)
        self._simulate_legacy_state()

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(self.task), {(self.secret.uuid, self.hidden.uuid)})
        self.assertEqual(self._refs(self.hidden), {(self.project.uuid, self.task.uuid)})

    def test_survives_a_deleted_task(self):
        create_link(self.hidden, self.task, "blocks", actor=self.admin)
        self._simulate_legacy_state()
        self.hidden.delete()

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(self.task), {(self.secret.uuid, None)})

    def test_a_renamed_key_leaves_the_pair_unresolved(self):
        create_link(self.hidden, self.task, "blocks", actor=self.admin)
        self._simulate_legacy_state()
        self.secret.key = "ZZZ"
        self.secret.save(update_fields=["key"])

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(self.task), {(None, None)})
        self.assertEqual(self._refs(self.hidden), {(None, None)})

    def test_an_event_without_its_partner_stays_unresolved(self):
        create_link(self.hidden, self.task, "blocks", actor=self.admin)
        self._simulate_legacy_state()
        self.hidden.events.filter(type__in=_LINK_TYPES).delete()

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(self.task), {(None, None)})

    def test_events_far_apart_are_not_paired(self):
        create_link(self.hidden, self.task, "blocks", actor=self.admin)
        self._simulate_legacy_state()
        event = self.hidden.events.get(type=TaskEvent.Type.LINKED)
        TaskEvent.objects.filter(pk=event.pk).update(
            created_at=event.created_at + timedelta(minutes=5)
        )

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(self.task), {(None, None)})

    def test_recorded_refs_are_left_alone(self):
        create_link(self.hidden, self.task, "blocks", actor=self.admin)
        before = self._refs(self.task)

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(self.task), before)

    def test_two_actors_linking_at_the_same_instant_each_get_their_own_pair(self):
        other = create_task(self.project, self.admin, title="Other")
        third = create_task(self.secret, self.admin, title="Third")
        create_link(self.hidden, self.task, "blocks", actor=self.admin)
        create_link(third, other, "blocks", actor=self.member)
        self._simulate_legacy_state()
        moment = TaskEvent.objects.filter(type__in=_LINK_TYPES).earliest("created_at")
        TaskEvent.objects.filter(type__in=_LINK_TYPES).update(
            created_at=moment.created_at
        )

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(self.task), {(self.secret.uuid, self.hidden.uuid)})
        self.assertEqual(self._refs(other), {(self.secret.uuid, third.uuid)})
        self.assertEqual(self._refs(third), {(self.project.uuid, other.uuid)})

    def test_interleaved_links_with_repeated_numbers_pair_their_own_ends(self):
        # Task numbers repeat across projects: four first tasks (all #1)
        # linked by one actor at once all name each other's number. The keys
        # in the references tell the two links apart.
        tasks = [
            create_task(create_project(self.admin, name=name), self.admin, title=name)
            for name in ("Alpha", "Beta", "Gamma", "Delta")
        ]
        create_link(tasks[0], tasks[1], "blocks", actor=self.admin)
        create_link(tasks[2], tasks[3], "blocks", actor=self.admin)
        self._simulate_legacy_state()
        moment = TaskEvent.objects.filter(type__in=_LINK_TYPES).earliest("created_at")
        # Interleave the two links' events so a first-match scan meets the
        # wrong partner first.
        for offset, task in enumerate((tasks[0], tasks[2], tasks[1], tasks[3])):
            task.events.filter(type__in=_LINK_TYPES).update(
                created_at=moment.created_at + timedelta(milliseconds=offset)
            )

        backfill_link_event_refs(apps, _SchemaEditor())

        for task, partner in zip(
            tasks, (tasks[1], tasks[0], tasks[3], tasks[2]), strict=True
        ):
            self.assertEqual(
                self._refs(task), {(partner.project_id, partner.pk)}, task.title
            )

    def test_two_orphans_are_not_paired_with_each_other(self):
        # Each link loses one side when its project is deleted (events
        # cascade with their project). The two survivors name each other's
        # number, but nothing else ties them: they must stay unresolved.
        alpha, beta, gamma, delta = (
            create_task(create_project(self.admin, name=name), self.admin, title=name)
            for name in ("Alpha", "Beta", "Gamma", "Delta")
        )
        create_link(alpha, beta, "blocks", actor=self.admin)
        create_link(gamma, delta, "blocks", actor=self.admin)
        self._simulate_legacy_state()
        beta.project.delete()
        delta.project.delete()

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(alpha), {(None, None)})
        self.assertEqual(self._refs(gamma), {(None, None)})

    def test_orphans_on_opposite_sides_are_not_paired_either(self):
        alpha, beta, gamma, delta = (
            create_task(create_project(self.admin, name=name), self.admin, title=name)
            for name in ("Alpha", "Beta", "Gamma", "Delta")
        )
        create_link(alpha, beta, "blocks", actor=self.admin)
        create_link(delta, gamma, "blocks", actor=self.admin)
        self._simulate_legacy_state()
        beta.project.delete()
        delta.project.delete()

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(alpha), {(None, None)})
        self.assertEqual(self._refs(gamma), {(None, None)})

    def test_orphans_are_not_paired_through_a_reused_key(self):
        alpha, beta, gamma, delta = (
            create_task(create_project(self.admin, name=name), self.admin, title=name)
            for name in ("Alpha", "Beta", "Gamma", "Delta")
        )
        create_link(alpha, beta, "blocks", actor=self.admin)
        create_link(delta, gamma, "blocks", actor=self.admin)
        self._simulate_legacy_state()
        freed_key = beta.project.key
        beta.project.delete()
        delta.project.delete()
        gamma.project.key = freed_key
        gamma.project.save(update_fields=["key"])

        backfill_link_event_refs(apps, _SchemaEditor())

        self.assertEqual(self._refs(alpha), {(None, None)})
        self.assertEqual(self._refs(gamma), {(None, None)})
