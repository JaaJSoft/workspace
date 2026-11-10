import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from workspace.files.models import File, FileOperation
from workspace.files.services import operations, trash, tree
from workspace.files.tasks import run_file_operation

User = get_user_model()

URL = "/api/v1/files/operations"


def _file(owner, name, parent, body=b"source"):
    return tree.create_file(
        owner, name, parent=parent, content=ContentFile(body, name=name)
    )


def _run_now(operation_uuid):
    return run_file_operation.apply(args=[operation_uuid])


class OperationApiFixture(TestCase):
    def setUp(self):
        cache.clear()
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.bob = User.objects.create_user(username="bob", password="pass")
        self.src = tree.create_folder(self.alice, "Src")
        self.dst = tree.create_folder(self.alice, "Dst")
        self.a = _file(self.alice, "a.txt", self.src, b"a")
        self.b = _file(self.alice, "b.txt", self.src, b"bb")
        self.client = APIClient()
        self.client.force_authenticate(self.alice)
        queued = patch.object(run_file_operation, "delay")
        self.queued = queued.start()
        self.addCleanup(queued.stop)

    def tearDown(self):
        cache.clear()

    def body(self, kind="move", nodes=None, target="dst", **item_fields):
        nodes = [self.a, self.b] if nodes is None else nodes
        if target == "dst":
            target = str(self.dst.pk)
        return {
            "kind": kind,
            "target": target,
            "items": [{"uuid": str(node.pk), **item_fields} for node in nodes],
        }

    def post(self, data):
        return self.client.post(URL, data, format="json")

    def operation_of(self, user, **fields):
        defaults = {
            "kind": FileOperation.Kind.MOVE,
            "items": [],
            "total": 0,
            "status": FileOperation.Status.COMPLETED,
            "finished_at": timezone.now(),
        }
        return FileOperation.objects.create(owner=user, **{**defaults, **fields})


class StartOperationTests(OperationApiFixture):
    def test_the_batch_is_accepted_and_handed_to_a_worker(self):
        response = self.post(self.body())

        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["kind"], "move")
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["processed"], 0)
        self.assertEqual(data["target"], str(self.dst.pk))
        self.assertEqual(data["target_name"], "Dst")
        self.assertFalse(data["cancel_requested"])
        self.queued.assert_called_once_with(data["uuid"])
        self.a.refresh_from_db()
        self.assertEqual(self.a.parent_id, self.src.pk)

    def test_with_an_eager_worker_the_answer_is_the_finished_report(self):
        self.queued.side_effect = _run_now

        response = self.post(self.body(kind="copy"))

        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["processed"], 2)
        self.assertEqual(data["counts"], {"done": 2})
        self.assertEqual(data["bytes_total"], 3)
        self.assertEqual(data["bytes_done"], 3)
        self.assertEqual(data["errors"], [])
        self.assertIsNotNone(data["finished_at"])
        self.assertEqual(
            sorted(File.objects.filter(parent=self.dst).values_list("name", flat=True)),
            ["a.txt", "b.txt"],
        )

    def test_the_root_is_a_destination_when_named_explicitly(self):
        self.queued.side_effect = _run_now

        response = self.post(self.body(target=None))

        self.assertEqual(response.status_code, 202)
        self.assertIsNone(response.json()["target"])
        self.a.refresh_from_db()
        self.assertIsNone(self.a.parent_id)

    def test_a_batch_no_worker_will_ever_run_is_not_left_behind(self):
        self.queued.side_effect = ConnectionError("broker down")

        with (
            self.assertRaises(ConnectionError),
            self.assertLogs("django.request", level="ERROR"),
        ):
            self.post(self.body())

        self.assertFalse(FileOperation.objects.exists())

    def test_malformed_requests_are_refused_before_anything_is_recorded(self):
        too_many = [
            {"uuid": str(uuid.uuid4())} for _ in range(operations.MAX_ITEMS + 1)
        ]
        cases = {
            "no target": (
                {k: v for k, v in self.body().items() if k != "target"},
                "target",
            ),
            "no items": ({**self.body(), "items": []}, "items"),
            "too many items": ({**self.body(), "items": too_many}, "items"),
            "an item twice": (self.body(nodes=[self.a, self.a]), "items"),
            "unknown kind": (self.body(kind="shred"), "kind"),
            "unknown conflict choice": (self.body(on_conflict="merge"), "items"),
            "malformed item": ({**self.body(), "items": [{"uuid": "nope"}]}, "items"),
            "malformed target": (self.body(target="nope"), "target"),
        }
        for label, (data, field) in cases.items():
            with self.subTest(label):
                response = self.post(data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json())
        self.assertFalse(FileOperation.objects.exists())
        self.queued.assert_not_called()

    def test_a_folder_out_of_reach_answers_like_one_that_does_not_exist(self):
        theirs = tree.create_folder(self.bob, "Private")

        hidden = self.post(self.body(target=str(theirs.pk)))
        absent = self.post(self.body(target=str(uuid.uuid4())))

        self.assertEqual(hidden.status_code, 400)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )
        self.assertFalse(FileOperation.objects.exists())

    def test_too_many_operations_in_progress(self):
        with patch.object(operations, "MAX_ACTIVE_PER_USER", 1):
            self.assertEqual(self.post(self.body(nodes=[self.a])).status_code, 202)

            response = self.post(self.body(nodes=[self.b]))

        self.assertEqual(response.status_code, 429)
        self.assertEqual(FileOperation.objects.count(), 1)

    def test_anonymous_callers_are_turned_away(self):
        self.client.force_authenticate(None)

        response = self.post(self.body())

        self.assertIn(response.status_code, (401, 403))
        self.assertFalse(FileOperation.objects.exists())


class ReadOperationTests(OperationApiFixture):
    def test_the_list_holds_the_callers_operations_most_recent_first(self):
        older = self.operation_of(self.alice)
        newer = self.operation_of(self.alice)
        self.operation_of(self.bob)

        response = self.client.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry["uuid"] for entry in response.json()],
            [str(newer.pk), str(older.pk)],
        )

    def test_the_report_explains_each_failure_in_words(self):
        self.queued.side_effect = _run_now
        _file(self.alice, "b.txt", self.dst)
        theirs = _file(self.bob, "secret.txt", None)

        response = self.post(self.body(nodes=[self.a, self.b, theirs]))

        data = response.json()
        self.assertEqual(data["counts"], {"done": 1, "failed": 2})
        self.assertEqual(
            data["errors"],
            [
                {
                    "uuid": str(self.b.pk),
                    "name": "b.txt",
                    "reason": "name_taken",
                    "message": "The destination already holds an item with this name.",
                },
                {
                    "uuid": str(theirs.pk),
                    "name": "",
                    "reason": "not_found",
                    "message": "This item no longer exists.",
                },
            ],
        )

    def test_a_reason_the_api_does_not_know_reads_as_a_plain_failure(self):
        operation = self.operation_of(
            self.alice,
            errors=[{"uuid": str(uuid.uuid4()), "name": "x", "reason": "gremlins"}],
        )

        response = self.client.get(f"{URL}/{operation.pk}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["errors"][0]["message"],
            "Something went wrong with this item.",
        )


class OperationOfSomeoneElseTests(OperationApiFixture):
    """Another user's operation answers exactly like one that does not exist."""

    def assertSameAnswer(self, method, suffix=""):
        theirs = self.operation_of(self.bob)
        call = getattr(self.client, method)

        hidden = call(f"{URL}/{theirs.pk}{suffix}")
        absent = call(f"{URL}/{uuid.uuid4()}{suffix}")

        self.assertEqual(hidden.status_code, 404)
        self.assertEqual(
            (hidden.status_code, hidden.content), (absent.status_code, absent.content)
        )
        return theirs

    def test_read(self):
        self.assertSameAnswer("get")

    def test_dismiss(self):
        theirs = self.assertSameAnswer("delete")
        self.assertTrue(FileOperation.objects.filter(pk=theirs.pk).exists())

    def test_cancel(self):
        theirs = self.assertSameAnswer("post", "/cancel")
        theirs.refresh_from_db()
        self.assertIsNone(theirs.cancel_requested_at)


class DismissAndCancelTests(OperationApiFixture):
    def test_a_finished_report_is_dismissed(self):
        operation = self.operation_of(self.alice)

        response = self.client.delete(f"{URL}/{operation.pk}")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(FileOperation.objects.filter(pk=operation.pk).exists())

    def test_an_operation_in_progress_cannot_be_dismissed(self):
        operation = self.operation_of(
            self.alice, status=FileOperation.Status.RUNNING, finished_at=None
        )

        response = self.client.delete(f"{URL}/{operation.pk}")

        self.assertEqual(response.status_code, 409)
        self.assertTrue(FileOperation.objects.filter(pk=operation.pk).exists())

    def test_an_operation_nobody_started_is_cancelled_at_once(self):
        operation = self.post(self.body()).json()

        response = self.client.post(f"{URL}/{operation['uuid']}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertTrue(response.json()["cancel_requested"])
        self.a.refresh_from_db()
        self.assertEqual(self.a.parent_id, self.src.pk)

    def test_a_running_operation_is_asked_to_stop(self):
        operation = self.operation_of(
            self.alice, status=FileOperation.Status.RUNNING, finished_at=None
        )

        response = self.client.post(f"{URL}/{operation.pk}/cancel")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "running")
        self.assertTrue(response.json()["cancel_requested"])


class KindShapeTests(OperationApiFixture):
    """What each kind of operation takes besides its items."""

    def test_a_kind_without_destination_refuses_one(self):
        for kind in ("trash", "restore", "purge"):
            with self.subTest(kind):
                response = self.post(self.body(kind=kind))
                self.assertEqual(response.status_code, 400)
                self.assertIn("target", response.json())

    def test_a_kind_without_destination_needs_none(self):
        response = self.post({"kind": "trash", "items": [{"uuid": str(self.a.pk)}]})

        self.assertEqual(response.status_code, 202)
        self.assertIsNone(response.json()["target"])

    def test_an_extraction_needs_a_destination(self):
        body = self.body(kind="extract", nodes=[self.a])
        del body["target"]

        response = self.post(body)

        self.assertEqual(response.status_code, 400)
        self.assertIn("target", response.json())

    def test_only_a_transfer_takes_a_conflict_choice(self):
        response = self.post(
            {
                "kind": "trash",
                "items": [{"uuid": str(self.a.pk), "on_conflict": "skip"}],
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("items", response.json())

    def test_emptying_the_trash_takes_no_items(self):
        response = self.post(
            {"kind": "empty_trash", "items": [{"uuid": str(self.a.pk)}]}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("items", response.json())

    def test_emptying_the_trash_runs_on_the_callers_trash(self):
        trash.soft_delete(self.src, acting_user=self.alice)
        self.queued.side_effect = _run_now

        response = self.post({"kind": "empty_trash"})

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "completed")
        self.assertEqual(response.json()["counts"], {"done": 1})
        self.assertFalse(File.objects.filter(pk=self.a.pk).exists())

    def test_every_other_kind_needs_items(self):
        for kind in ("trash", "restore", "purge"):
            with self.subTest(kind):
                response = self.post({"kind": kind, "items": []})
                self.assertEqual(response.status_code, 400)
                self.assertIn("items", response.json())
