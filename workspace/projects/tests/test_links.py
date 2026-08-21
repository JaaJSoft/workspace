from unittest.mock import patch

from django.test import TestCase

from workspace.projects.models import TaskEvent, TaskLink, TaskStatus
from workspace.projects.services.links import (
    annotate_blocked,
    create_link,
    delete_link,
    links_for_task,
)
from workspace.projects.services.members import ProjectRuleError
from workspace.projects.services.projects import create_project
from workspace.projects.services.tasks import create_task

from .base import ProjectTestMixin


class CreateLinkTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task_a = create_task(self.project, self.admin, title="A")
        self.task_b = create_task(self.project, self.admin, title="B")
        self.task_c = create_task(self.project, self.admin, title="C")

    def test_forward_relation_stores_canonical_direction(self):
        link = create_link(self.task_a, self.task_b, "blocks", actor=self.admin)
        self.assertEqual(link.source, self.task_a)
        self.assertEqual(link.target, self.task_b)
        self.assertEqual(link.type, TaskLink.Type.BLOCKS)

    def test_reversed_relation_swaps_the_ends(self):
        link = create_link(self.task_a, self.task_b, "blocked_by", actor=self.admin)
        self.assertEqual(link.source, self.task_b)
        self.assertEqual(link.target, self.task_a)
        self.assertEqual(link.type, TaskLink.Type.BLOCKS)

    def test_self_link_is_rejected(self):
        with self.assertRaises(ProjectRuleError):
            create_link(self.task_a, self.task_a, "relates_to")

    def test_same_type_duplicate_is_rejected_in_both_directions(self):
        create_link(self.task_a, self.task_b, "relates_to")
        with self.assertRaises(ProjectRuleError):
            create_link(self.task_a, self.task_b, "relates_to")
        with self.assertRaises(ProjectRuleError):
            create_link(self.task_b, self.task_a, "relates_to")

    def test_different_type_between_same_tasks_is_allowed(self):
        create_link(self.task_a, self.task_b, "relates_to")
        create_link(self.task_a, self.task_b, "duplicates")
        self.assertEqual(TaskLink.objects.count(), 2)

    def test_transitive_block_cycle_is_rejected(self):
        create_link(self.task_a, self.task_b, "blocks")
        create_link(self.task_b, self.task_c, "blocks")
        with self.assertRaises(ProjectRuleError):
            create_link(self.task_c, self.task_a, "blocks")

    def test_cycle_check_covers_reversed_relations(self):
        create_link(self.task_a, self.task_b, "blocks")
        # "A is blocked by B" would store B->A, closing the loop.
        with self.assertRaises(ProjectRuleError):
            create_link(self.task_a, self.task_b, "blocked_by")

    def test_block_chain_without_cycle_is_allowed(self):
        create_link(self.task_a, self.task_b, "blocks")
        create_link(self.task_b, self.task_c, "blocks")
        create_link(self.task_a, self.task_c, "blocks")
        self.assertEqual(TaskLink.objects.count(), 3)

    def test_link_records_one_event_per_end(self):
        create_link(self.task_a, self.task_b, "blocks", actor=self.admin)
        event_a = self.task_a.events.get(type=TaskEvent.Type.LINKED)
        event_b = self.task_b.events.get(type=TaskEvent.Type.LINKED)
        self.assertEqual(event_a.from_value, "blocks")
        self.assertEqual(event_a.to_value, self.task_b.reference)
        self.assertEqual(event_b.from_value, "is blocked by")
        self.assertEqual(event_b.to_value, self.task_a.reference)

    def test_delete_link_records_unlinked_events(self):
        link = create_link(self.task_a, self.task_b, "duplicates", actor=self.admin)
        delete_link(link, actor=self.admin)
        self.assertFalse(TaskLink.objects.exists())
        self.assertTrue(
            self.task_a.events.filter(
                type=TaskEvent.Type.UNLINKED, to_value=self.task_b.reference
            ).exists()
        )
        self.assertTrue(
            self.task_b.events.filter(
                type=TaskEvent.Type.UNLINKED, to_value=self.task_a.reference
            ).exists()
        )

    def test_task_deletion_cascades_links_but_keeps_events(self):
        create_link(self.task_a, self.task_b, "blocks", actor=self.admin)
        reference = self.task_b.reference
        self.task_b.delete()
        self.assertFalse(TaskLink.objects.exists())
        event = self.task_a.events.get(type=TaskEvent.Type.LINKED)
        self.assertEqual(event.to_value, reference)


class LinksForTaskTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.task_a = create_task(self.project, self.admin, title="A")
        self.task_b = create_task(self.project, self.admin, title="B")

    def test_both_directions_fold_into_one_list(self):
        create_link(self.task_a, self.task_b, "blocks")
        outward = links_for_task(self.member, self.task_a)
        inward = links_for_task(self.member, self.task_b)
        self.assertEqual(len(outward), 1)
        self.assertEqual(outward[0]["label"], "blocks")
        self.assertEqual(outward[0]["task"]["uuid"], str(self.task_b.uuid))
        self.assertEqual(len(inward), 1)
        self.assertEqual(inward[0]["label"], "is blocked by")
        self.assertEqual(inward[0]["task"]["uuid"], str(self.task_a.uuid))

    def test_inaccessible_other_end_is_hidden(self):
        other_project = create_project(self.admin, name="Secret")
        secret = create_task(other_project, self.admin, title="Secret task")
        create_link(self.task_a, secret, "relates_to")
        self.assertEqual(links_for_task(self.member, self.task_a), [])
        # The admin can see both projects, so the link shows for them.
        self.assertEqual(len(links_for_task(self.admin, self.task_a)), 1)

    def test_done_state_is_serialized(self):
        done = self.project.statuses.get(category=TaskStatus.Category.DONE)
        self.task_b.status = done
        self.task_b.save(update_fields=["status"])
        create_link(self.task_a, self.task_b, "blocks")
        (item,) = links_for_task(self.member, self.task_a)
        self.assertTrue(item["task"]["is_done"])


class AnnotateBlockedTests(ProjectTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.blocker = create_task(self.project, self.admin, title="Blocker")
        self.blocked = create_task(self.project, self.admin, title="Blocked")

    def _is_blocked(self, task):
        return annotate_blocked(self.project.tasks.all()).get(pk=task.pk).is_blocked

    def test_open_blocker_marks_the_target(self):
        create_link(self.blocker, self.blocked, "blocks")
        self.assertTrue(self._is_blocked(self.blocked))
        self.assertFalse(self._is_blocked(self.blocker))

    def test_done_blocker_does_not_count(self):
        create_link(self.blocker, self.blocked, "blocks")
        done = self.project.statuses.get(category=TaskStatus.Category.DONE)
        self.blocker.status = done
        self.blocker.save(update_fields=["status"])
        self.assertFalse(self._is_blocked(self.blocked))

    def test_non_blocking_types_do_not_count(self):
        create_link(self.blocker, self.blocked, "relates_to")
        create_link(self.blocker, self.blocked, "duplicates")
        self.assertFalse(self._is_blocked(self.blocked))


class CreateLinkConcurrencyTests(ProjectTestMixin, TestCase):
    """The duplicate pre-check can be raced past; the constraint and the
    transaction are the backstop."""

    def setUp(self):
        super().setUp()
        self.task_a = create_task(self.project, self.admin, title="A")
        self.task_b = create_task(self.project, self.admin, title="B")

    def test_constraint_race_maps_to_the_duplicate_rule_error(self):
        # Simulate the lost race: the pre-check misses the concurrent row,
        # so the unique constraint is what fires.
        create_link(self.task_a, self.task_b, "blocks")
        with patch(
            "workspace.projects.services.links._same_type_link_exists",
            return_value=False,
        ):
            with self.assertRaises(ProjectRuleError):
                create_link(self.task_a, self.task_b, "blocks")
        self.assertEqual(TaskLink.objects.count(), 1)

    def test_failed_event_write_rolls_the_link_back(self):
        with patch(
            "workspace.projects.services.links.record_task_event",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                create_link(self.task_a, self.task_b, "blocks")
        self.assertFalse(TaskLink.objects.exists())

    def test_failed_event_write_keeps_the_link_on_delete(self):
        link = create_link(self.task_a, self.task_b, "blocks")
        with patch(
            "workspace.projects.services.links.record_task_event",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                delete_link(link)
        self.assertTrue(TaskLink.objects.exists())
