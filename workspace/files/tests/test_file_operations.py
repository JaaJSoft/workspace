import uuid
from datetime import timedelta
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from workspace.files.models import File, FileOperation, FileShare
from workspace.files.services import FileService, operations
from workspace.files.services.operations import (
    Slice,
    TargetUnavailable,
    TooManyOperations,
    create_operation,
    fail_stale_operations,
    purge_finished_operations,
    request_cancel,
    run_slice,
)
from workspace.files.services.transfers import move_item
from workspace.files.sse_provider import FilesSSEProvider
from workspace.files.tasks import run_file_operation, sweep_file_operations

User = get_user_model()

COPY = FileOperation.Kind.COPY
MOVE = FileOperation.Kind.MOVE
Status = FileOperation.Status


def _file(owner, name, parent, body=b"source"):
    return FileService.create_file(
        owner, name, parent=parent, content=ContentFile(body, name=name)
    )


def _names(parent):
    return sorted(
        File.objects.filter(parent=parent, deleted_at__isnull=True).values_list(
            "name", flat=True
        )
    )


class OperationFixture(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.src = FileService.create_folder(self.alice, "Src")
        self.dst = FileService.create_folder(self.alice, "Dst")
        self.files = [
            _file(self.alice, name, self.src, body)
            for name, body in (("a.txt", b"a"), ("b.txt", b"bb"), ("c.txt", b"ccc"))
        ]

    def tearDown(self):
        cache.clear()

    def start(self, kind, nodes, target, user=None, on_conflict=None):
        items = [
            {"uuid": getattr(node, "pk", node), "on_conflict": on_conflict}
            for node in nodes
        ]
        target_uuid = target.pk if target is not None else None
        return create_operation(user or self.alice, kind, items, target_uuid)

    def run_to_end(self, operation):
        result = run_slice(operation.pk)
        operation.refresh_from_db()
        return result

    def share(self, node, owner, with_user, permission):
        return FileShare.objects.create(
            file=node, shared_by=owner, shared_with=with_user, permission=permission
        )


class CreateOperationTests(OperationFixture):
    def test_the_batch_is_recorded_and_waits_for_a_worker(self):
        operation = self.start(MOVE, self.files, self.dst)

        self.assertEqual(operation.status, Status.PENDING)
        self.assertEqual(operation.total, 3)
        self.assertEqual(operation.target_uuid, self.dst.pk)
        self.assertEqual(operation.target_name, "Dst")
        self.assertEqual(
            [item["uuid"] for item in operation.items],
            [str(node.pk) for node in self.files],
        )
        self.assertEqual(_names(self.src), ["a.txt", "b.txt", "c.txt"])

    def test_a_move_refuses_a_taken_name_unless_told_otherwise(self):
        operation = self.start(MOVE, self.files[:1], self.dst)
        self.assertEqual(operation.items[0]["on_conflict"], "error")

    def test_a_copy_renames_on_a_taken_name_unless_told_otherwise(self):
        operation = self.start(COPY, self.files[:1], self.dst)
        self.assertEqual(operation.items[0]["on_conflict"], "rename")

    def test_the_choice_made_for_an_item_is_kept(self):
        operation = self.start(MOVE, self.files[:1], self.dst, on_conflict="skip")
        self.assertEqual(operation.items[0]["on_conflict"], "skip")

    def test_the_root_is_a_destination(self):
        operation = self.start(MOVE, self.files, None)
        self.assertIsNone(operation.target_uuid)
        self.assertEqual(operation.target_name, "")

    def test_a_folder_out_of_reach_is_no_destination(self):
        theirs = FileService.create_folder(self.bob, "Private")
        read_only = FileService.create_folder(self.bob, "Shown")
        self.share(read_only, self.bob, self.alice, FileShare.Permission.READ_ONLY)
        trashed = FileService.create_folder(self.alice, "Gone")
        FileService.soft_delete(trashed, acting_user=self.alice)

        for label, target_uuid in (
            ("unknown", uuid.uuid4()),
            ("someone else's", theirs.pk),
            ("read-only", read_only.pk),
            ("trashed", trashed.pk),
            ("a file", self.files[0].pk),
        ):
            with self.subTest(label), self.assertRaises(TargetUnavailable):
                create_operation(
                    self.alice, MOVE, [{"uuid": self.files[1].pk}], target_uuid
                )
        self.assertFalse(FileOperation.objects.exists())

    def test_operations_in_progress_are_capped_per_user(self):
        with patch.object(operations, "MAX_ACTIVE_PER_USER", 2):
            first = self.start(MOVE, self.files[:1], self.dst)
            self.start(MOVE, self.files[1:2], self.dst)
            with self.assertRaises(TooManyOperations):
                self.start(MOVE, self.files[2:], self.dst)

            theirs = FileService.create_folder(self.bob, "Theirs")
            self.start(MOVE, [uuid.uuid4()], theirs, user=self.bob)

            self.run_to_end(first)
            self.start(MOVE, self.files[2:], self.dst)


class RunMoveTests(OperationFixture):
    def test_every_item_ends_up_in_the_destination(self):
        operation = self.start(MOVE, self.files, self.dst)

        self.assertEqual(self.run_to_end(operation), Slice.FINISHED)

        self.assertEqual(operation.status, Status.COMPLETED)
        self.assertEqual(operation.processed, 3)
        self.assertEqual(operation.counts, {"done": 3})
        self.assertEqual(operation.errors, [])
        self.assertIsNotNone(operation.finished_at)
        self.assertIsNone(operation.bytes_total)
        self.assertEqual(_names(self.dst), ["a.txt", "b.txt", "c.txt"])
        self.assertEqual(_names(self.src), [])

    def test_an_item_that_cannot_go_through_does_not_stop_the_others(self):
        _file(self.alice, "b.txt", self.dst, b"already here")
        operation = self.start(MOVE, self.files, self.dst)

        self.run_to_end(operation)

        self.assertEqual(operation.status, Status.COMPLETED)
        self.assertEqual(operation.processed, 3)
        self.assertEqual(operation.counts, {"done": 2, "failed": 1})
        self.assertEqual(
            operation.errors,
            [
                {
                    "uuid": str(self.files[1].pk),
                    "name": "b.txt",
                    "reason": "name_taken",
                }
            ],
        )
        self.assertEqual(_names(self.src), ["b.txt"])

    def test_each_item_follows_its_own_conflict_choice(self):
        _file(self.alice, "a.txt", self.dst)
        _file(self.alice, "b.txt", self.dst)
        _file(self.alice, "c.txt", self.dst)
        operation = create_operation(
            self.alice,
            MOVE,
            [
                {"uuid": self.files[0].pk, "on_conflict": "skip"},
                {"uuid": self.files[1].pk, "on_conflict": "replace"},
                {"uuid": self.files[2].pk, "on_conflict": "rename"},
            ],
            self.dst.pk,
        )

        self.run_to_end(operation)

        self.assertEqual(operation.counts, {"skipped": 1, "replaced": 1, "renamed": 1})
        self.assertEqual(_names(self.src), ["a.txt"])
        self.assertEqual(len(_names(self.dst)), 4)

    def test_an_item_out_of_reach_is_reported_like_one_that_does_not_exist(self):
        theirs = _file(self.bob, "secret.txt", None)
        unknown = uuid.uuid4()
        operation = self.start(MOVE, [theirs, unknown], self.dst)

        self.run_to_end(operation)

        hidden, absent = operation.errors
        self.assertEqual(hidden["uuid"], str(theirs.pk))
        self.assertEqual(absent["uuid"], str(unknown))
        self.assertEqual(
            {**hidden, "uuid": None},
            {"uuid": None, "name": "", "reason": "not_found"},
        )
        self.assertEqual({**hidden, "uuid": None}, {**absent, "uuid": None})
        theirs.refresh_from_db()
        self.assertIsNone(theirs.parent_id)
        self.assertEqual(theirs.owner_id, self.bob.pk)

    def test_a_trashed_item_is_reported_as_gone(self):
        FileService.soft_delete(self.files[0], acting_user=self.alice)
        operation = self.start(MOVE, self.files[:1], self.dst)

        self.run_to_end(operation)

        self.assertEqual(operation.errors[0]["reason"], "not_found")
        self.assertEqual(operation.errors[0]["name"], "")

    def test_an_item_the_user_can_only_read_is_named_and_refused(self):
        shown = _file(self.bob, "shown.txt", None)
        self.share(shown, self.bob, self.alice, FileShare.Permission.READ_ONLY)
        operation = self.start(MOVE, [shown], self.dst)

        self.run_to_end(operation)

        self.assertEqual(
            operation.errors,
            [{"uuid": str(shown.pk), "name": "shown.txt", "reason": "not_allowed"}],
        )

    def test_access_is_judged_when_the_item_runs_not_when_it_was_asked(self):
        shared = _file(self.bob, "shared.txt", None)
        grant = self.share(
            shared, self.bob, self.alice, FileShare.Permission.READ_WRITE
        )
        operation = self.start(COPY, [shared], self.dst)
        grant.delete()

        self.run_to_end(operation)

        self.assertEqual(operation.errors[0]["reason"], "not_found")
        self.assertEqual(_names(self.dst), [])


class DestinationGoneTests(OperationFixture):
    """A destination lost after the request must never turn into the root."""

    def assertNothingMoved(self, operation):
        self.assertEqual(operation.status, Status.COMPLETED)
        self.assertEqual(operation.counts, {"failed": 3})
        self.assertEqual({error["reason"] for error in operation.errors}, {"target"})
        self.assertEqual(_names(self.src), ["a.txt", "b.txt", "c.txt"])
        self.assertEqual(_names(None).count("a.txt"), 0)

    def test_destination_trashed_before_the_run(self):
        operation = self.start(MOVE, self.files, self.dst)
        FileService.soft_delete(self.dst, acting_user=self.alice)

        self.run_to_end(operation)

        self.assertNothingMoved(operation)

    def test_destination_deleted_for_good_before_the_run(self):
        operation = self.start(MOVE, self.files, self.dst)
        File.objects.filter(pk=self.dst.pk).delete()

        self.run_to_end(operation)

        self.assertNothingMoved(operation)

    def test_access_to_the_destination_withdrawn_before_the_run(self):
        team = Group.objects.create(name="Design")
        self.alice.groups.add(team)
        self.bob.groups.add(team)
        shared = FileService.create_folder(self.bob, "Design", group=team)
        operation = self.start(COPY, self.files, shared)
        self.alice.groups.remove(team)

        self.run_to_end(operation)

        self.assertEqual(operation.counts, {"failed": 3})
        self.assertEqual({error["reason"] for error in operation.errors}, {"target"})
        self.assertEqual(_names(shared), [])

    def test_destination_lost_between_two_items(self):
        operation = self.start(MOVE, self.files, self.dst)

        def move_then_trash(user, node, parent, on_conflict):
            result = move_item(user, node, parent, on_conflict)
            File.objects.filter(pk=self.dst.pk).update(deleted_at=timezone.now())
            return result

        with patch.object(operations, "move_item", move_then_trash):
            self.run_to_end(operation)

        self.assertEqual(operation.counts, {"done": 1, "failed": 2})
        self.assertEqual(_names(self.src), ["b.txt", "c.txt"])


class RunCopyTests(OperationFixture):
    def test_the_bytes_to_write_are_measured_and_all_accounted_for(self):
        folder = FileService.create_folder(self.alice, "Album", parent=self.src)
        _file(self.alice, "inside.txt", folder, b"dddd")
        operation = self.start(COPY, [*self.files, folder], self.dst)

        self.run_to_end(operation)

        self.assertEqual(operation.status, Status.COMPLETED)
        self.assertEqual(operation.bytes_total, 1 + 2 + 3 + 4)
        self.assertEqual(operation.bytes_done, operation.bytes_total)
        self.assertEqual(operation.counts, {"done": 4})
        self.assertEqual(_names(self.dst), ["Album", "a.txt", "b.txt", "c.txt"])
        self.assertEqual(_names(self.src), ["Album", "a.txt", "b.txt", "c.txt"])

    def test_what_the_user_cannot_see_weighs_nothing(self):
        theirs = _file(self.bob, "secret.bin", None, b"x" * 500)
        operation = self.start(COPY, [self.files[0], theirs], self.dst)

        with patch.object(operations._Runner, "_finish"):
            run_slice(operation.pk)
        operation.refresh_from_db()

        self.assertEqual(operation.bytes_total, 1)

    def test_a_copy_cancelled_midway_leaves_nothing_of_the_item_in_flight(self):
        folder = FileService.create_folder(self.alice, "Album", parent=self.src)
        for name in ("one.txt", "two.txt", "three.txt"):
            _file(self.alice, name, folder)
        operation = self.start(COPY, [folder], self.dst)
        copied = operations._Runner._copied

        def cancel_then_check(runner, node):
            FileOperation.objects.filter(pk=operation.pk).update(
                cancel_requested_at=timezone.now()
            )
            return copied(runner, node)

        with (
            patch.object(operations._Runner, "_copied", cancel_then_check),
            patch.object(operations, "_PROGRESS_INTERVAL", 0),
        ):
            result = self.run_to_end(operation)

        self.assertEqual(result, Slice.FINISHED)
        self.assertEqual(operation.status, Status.CANCELLED)
        self.assertEqual(operation.processed, 0)
        self.assertEqual(operation.bytes_done, 0)
        self.assertEqual(operation.counts, {})
        self.assertFalse(File.objects.filter(parent=self.dst).exists())
        self.assertEqual(File.objects.filter(parent=folder).count(), 3)


class ItemFailureTests(OperationFixture):
    def test_an_unexpected_error_fails_the_item_and_says_nothing_of_itself(self):
        operation = self.start(MOVE, self.files, self.dst)
        calls = []

        def fail_first(user, node, parent, on_conflict):
            calls.append(node.pk)
            if len(calls) == 1:
                raise RuntimeError("/srv/media/files/users/alice/a.txt vanished")
            return move_item(user, node, parent, on_conflict)

        with (
            patch.object(operations, "move_item", fail_first),
            self.assertLogs(operations.logger, level="ERROR"),
        ):
            self.run_to_end(operation)

        self.assertEqual(operation.status, Status.COMPLETED)
        self.assertEqual(operation.counts, {"failed": 1, "done": 2})
        self.assertEqual(
            operation.errors,
            [{"uuid": str(self.files[0].pk), "name": "a.txt", "reason": "failed"}],
        )

    def test_a_worker_running_out_of_time_fails_the_item_and_hands_over(self):
        operation = self.start(MOVE, self.files, self.dst)

        with patch.object(operations, "move_item", side_effect=SoftTimeLimitExceeded):
            result = self.run_to_end(operation)

        self.assertEqual(result, Slice.PAUSED)
        self.assertEqual(operation.status, Status.RUNNING)
        self.assertEqual(operation.processed, 1)
        self.assertEqual(operation.errors[0]["reason"], "timeout")

        self.assertEqual(self.run_to_end(operation), Slice.FINISHED)
        self.assertEqual(operation.counts, {"failed": 1, "done": 2})

    def test_the_list_of_errors_is_capped_but_the_count_is_not(self):
        operation = self.start(MOVE, [uuid.uuid4() for _ in range(5)], self.dst)

        with patch.object(operations, "_MAX_ERRORS", 2):
            self.run_to_end(operation)

        self.assertEqual(len(operation.errors), 2)
        self.assertEqual(operation.counts, {"failed": 5})
        self.assertEqual(operation.processed, 5)


class SliceTests(OperationFixture):
    def test_a_slice_out_of_time_pauses_and_the_next_one_resumes(self):
        operation = self.start(MOVE, self.files, self.dst)

        with patch.object(operations, "SLICE_SECONDS", -1):
            self.assertEqual(self.run_to_end(operation), Slice.PAUSED)
            self.assertEqual(operation.status, Status.RUNNING)
            self.assertEqual(operation.processed, 1)
            self.assertEqual(_names(self.dst), ["a.txt"])

            self.assertEqual(self.run_to_end(operation), Slice.PAUSED)
            self.assertEqual(operation.processed, 2)

            self.assertEqual(self.run_to_end(operation), Slice.FINISHED)

        self.assertEqual(operation.status, Status.COMPLETED)
        self.assertEqual(operation.counts, {"done": 3})
        self.assertEqual(_names(self.dst), ["a.txt", "b.txt", "c.txt"])

    def test_a_finished_operation_is_not_run_again(self):
        operation = self.start(COPY, self.files, self.dst)
        self.run_to_end(operation)

        self.assertEqual(self.run_to_end(operation), Slice.FINISHED)

        self.assertEqual(_names(self.dst), ["a.txt", "b.txt", "c.txt"])

    def test_an_operation_that_no_longer_exists_is_nothing_to_run(self):
        self.assertEqual(run_slice(uuid.uuid4()), Slice.FINISHED)

    def test_the_operation_of_a_deactivated_user_fails_untouched(self):
        operation = self.start(MOVE, self.files, self.dst)
        User.objects.filter(pk=self.alice.pk).update(is_active=False)

        self.run_to_end(operation)

        self.assertEqual(operation.status, Status.FAILED)
        self.assertEqual(operation.processed, 0)
        self.assertIsNotNone(operation.finished_at)
        self.assertEqual(_names(self.src), ["a.txt", "b.txt", "c.txt"])

    def test_an_operation_deleted_under_the_runner_stops_it_quietly(self):
        operation = self.start(MOVE, self.files, self.dst)

        def move_then_vanish(user, node, parent, on_conflict):
            result = move_item(user, node, parent, on_conflict)
            FileOperation.objects.filter(pk=operation.pk).delete()
            return result

        with (
            patch.object(operations, "move_item", move_then_vanish),
            patch.object(operations, "_PROGRESS_INTERVAL", 0),
        ):
            result = run_slice(operation.pk)

        self.assertEqual(result, Slice.FINISHED)
        self.assertEqual(_names(self.dst), ["a.txt"])


class CancelTests(OperationFixture):
    def test_an_operation_nobody_started_is_cancelled_on_the_spot(self):
        operation = self.start(MOVE, self.files, self.dst)

        operation = request_cancel(operation)

        self.assertEqual(operation.status, Status.CANCELLED)
        self.assertIsNotNone(operation.finished_at)
        self.assertEqual(self.run_to_end(operation), Slice.FINISHED)
        self.assertEqual(operation.status, Status.CANCELLED)
        self.assertEqual(_names(self.src), ["a.txt", "b.txt", "c.txt"])

    def test_a_running_operation_stops_after_the_item_in_flight(self):
        operation = self.start(MOVE, self.files, self.dst)

        def move_then_cancel(user, node, parent, on_conflict):
            result = move_item(user, node, parent, on_conflict)
            request_cancel(FileOperation.objects.get(pk=operation.pk))
            return result

        with (
            patch.object(operations, "move_item", move_then_cancel),
            patch.object(operations, "_PROGRESS_INTERVAL", 0),
        ):
            result = self.run_to_end(operation)

        self.assertEqual(result, Slice.FINISHED)
        self.assertEqual(operation.status, Status.CANCELLED)
        self.assertEqual(operation.processed, 1)
        self.assertEqual(operation.counts, {"done": 1})
        self.assertIsNotNone(operation.finished_at)
        self.assertEqual(_names(self.dst), ["a.txt"])
        self.assertEqual(_names(self.src), ["b.txt", "c.txt"])

    def test_cancelling_a_finished_operation_changes_nothing(self):
        operation = self.start(MOVE, self.files, self.dst)
        self.run_to_end(operation)
        finished_at = operation.finished_at

        operation = request_cancel(operation)

        self.assertEqual(operation.status, Status.COMPLETED)
        self.assertIsNone(operation.cancel_requested_at)
        self.assertEqual(operation.finished_at, finished_at)


class ProgressEventTests(OperationFixture):
    def events(self, stream):
        return [payload for _name, payload, _id in stream.poll("dirty")]

    def test_the_owner_is_told_how_it_ended_and_nobody_else(self):
        mine = FilesSSEProvider(self.alice, None)
        theirs = FilesSSEProvider(self.bob, None)
        _file(self.alice, "b.txt", self.dst)
        operation = self.start(MOVE, self.files, self.dst)

        self.run_to_end(operation)

        last = [e for e in self.events(mine) if e["type"] == "operation"][-1]
        self.assertEqual(
            last,
            {
                "type": "operation",
                "operation_uuid": str(operation.pk),
                "kind": "move",
                "status": "completed",
                "total": 3,
                "processed": 3,
                "bytes_total": None,
                "bytes_done": 0,
                "counts": {"done": 2, "failed": 1},
            },
        )
        self.assertEqual(
            [e for e in self.events(theirs) if e["type"] == "operation"], []
        )

    def test_a_stream_only_reads_the_latest_progress_of_an_operation(self):
        mine = FilesSSEProvider(self.alice, None)
        operation = self.start(MOVE, self.files, self.dst)

        with patch.object(operations, "_PROGRESS_INTERVAL", 0):
            self.run_to_end(operation)

        progress = [e for e in self.events(mine) if e["type"] == "operation"]
        self.assertEqual([e["status"] for e in progress], ["completed"])

    def test_progress_is_published_while_it_runs(self):
        operation = self.start(MOVE, self.files, self.dst)

        with (
            patch.object(operations, "_PROGRESS_INTERVAL", 0),
            patch.object(operations, "push_user_event") as push,
        ):
            self.run_to_end(operation)

        seen = [call.args[2]["processed"] for call in push.call_args_list]
        self.assertEqual(seen, [0, 1, 2, 3])


class RecoveryTests(OperationFixture):
    def test_an_operation_whose_worker_is_gone_is_closed_not_resumed(self):
        lost = self.start(MOVE, self.files[:1], self.dst)
        never_started = self.start(MOVE, self.files[1:2], self.dst)
        alive = self.start(MOVE, self.files[2:], self.dst)
        long_ago = timezone.now() - operations.STALE_AFTER - timedelta(minutes=1)
        FileOperation.objects.filter(pk=lost.pk).update(
            status=Status.RUNNING, heartbeat_at=long_ago
        )
        FileOperation.objects.filter(pk=never_started.pk).update(heartbeat_at=long_ago)
        FileOperation.objects.filter(pk=alive.pk).update(status=Status.RUNNING)
        stream = FilesSSEProvider(self.alice, None)

        self.assertEqual(fail_stale_operations(), 2)

        for operation in (lost, never_started):
            operation.refresh_from_db()
            self.assertEqual(operation.status, Status.FAILED)
            self.assertIsNotNone(operation.finished_at)
        alive.refresh_from_db()
        self.assertEqual(alive.status, Status.RUNNING)
        self.assertEqual(
            {
                (payload["operation_uuid"], payload["status"])
                for _name, payload, _id in stream.poll("dirty")
            },
            {(str(lost.pk), "failed"), (str(never_started.pk), "failed")},
        )
        self.assertEqual(self.run_to_end(lost), Slice.FINISHED)
        self.assertEqual(_names(self.src), ["a.txt", "b.txt", "c.txt"])

    def test_old_reports_are_dropped_and_recent_or_running_ones_kept(self):
        old = self.start(MOVE, self.files[:1], self.dst)
        recent = self.start(MOVE, self.files[1:2], self.dst)
        running = self.start(MOVE, self.files[2:], self.dst)
        self.run_to_end(old)
        self.run_to_end(recent)
        FileOperation.objects.filter(pk=old.pk).update(
            finished_at=timezone.now() - operations.RETENTION - timedelta(minutes=1)
        )

        self.assertEqual(purge_finished_operations(), 1)

        self.assertEqual(
            set(FileOperation.objects.values_list("pk", flat=True)),
            {recent.pk, running.pk},
        )

    def test_the_sweep_does_both(self):
        lost = self.start(MOVE, self.files[:1], self.dst)
        old = self.start(MOVE, self.files[1:2], self.dst)
        self.run_to_end(old)
        long_ago = timezone.now() - operations.RETENTION - timedelta(minutes=1)
        FileOperation.objects.filter(pk=lost.pk).update(heartbeat_at=long_ago)
        FileOperation.objects.filter(pk=old.pk).update(finished_at=long_ago)

        with self.assertLogs("workspace.files.tasks", level="WARNING"):
            result = sweep_file_operations()

        self.assertEqual(result, {"failed": 1, "purged": 1})


class TaskTests(OperationFixture):
    def test_without_a_broker_the_slices_run_one_after_the_other(self):
        operation = self.start(MOVE, self.files, self.dst)

        with patch.object(operations, "SLICE_SECONDS", -1):
            result = run_file_operation.apply(args=[str(operation.pk)])

        self.assertEqual(result.get(), {"status": "finished"})
        operation.refresh_from_db()
        self.assertEqual(operation.status, Status.COMPLETED)
        self.assertEqual(operation.processed, 3)

    def test_on_a_worker_a_paused_operation_is_sent_back_to_the_queue(self):
        operation = self.start(MOVE, self.files, self.dst)

        with (
            patch.object(operations, "SLICE_SECONDS", -1),
            patch.object(run_file_operation, "apply_async") as requeue,
        ):
            result = run_file_operation(str(operation.pk))

        self.assertEqual(result, {"status": "paused"})
        requeue.assert_called_once_with(args=[str(operation.pk)], countdown=0)
        operation.refresh_from_db()
        self.assertEqual(operation.processed, 1)
