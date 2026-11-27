import uuid
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from rest_framework import serializers
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from workspace.common.views import (
    AvailableActionsView,
    BatchView,
    LinkView,
    ReorderView,
)

User = get_user_model()


class _Rows:
    """Stands in for the queryset of the rows a caller can reach."""

    def __init__(self, *names):
        self.rows = [SimpleNamespace(pk=uuid.uuid4(), name=name) for name in names]

    def filter(self, pk__in):
        return [row for row in self.rows if row.pk in pk__in]

    def named(self, name):
        return next(row.pk for row in self.rows if row.name == name)


class _RowSerializer(serializers.Serializer):
    uuid = serializers.UUIDField(source="pk")
    name = serializers.CharField()


class _RenameSerializer(serializers.Serializer):
    name = serializers.CharField()


class ApiViewTestCase(TestCase):
    view_class = None

    def setUp(self):
        self.user = User.objects.create_user(username="caller")
        self.rows = _Rows("a", "b", "c")
        self.calls = []

    def _send(self, method, body=None, **kwargs):
        request = getattr(APIRequestFactory(), method)("/", body, format="json")
        force_authenticate(request, user=self.user)
        view = self.view_class.as_view(rows=self.rows, calls=self.calls)
        return view(request, **kwargs)


class _Batch(BatchView):
    permission_classes = []
    batch_name = "Row"
    batch_actions = {"rename": _RenameSerializer, "delete": None, "fail": None}
    serializer_class = _RowSerializer
    rows = None
    calls = None

    def get_queryset(self):
        return self.rows

    def perform_rename(self, rows, data):
        self.calls.append(("rename", [row.name for row in rows], dict(data)))
        return rows

    def perform_delete(self, rows, data):
        self.calls.append(("delete", [row.name for row in rows], dict(data)))

    def perform_fail(self, rows, data):
        Group.objects.create(name="written before the refusal")
        raise ValidationError({"uuids": ["refused"]})


class BatchViewTests(ApiViewTestCase):
    view_class = _Batch

    def _batch(self, action, names, **extra):
        uuids = [str(self.rows.named(name)) for name in names]
        return self._send("post", {"action": action, "uuids": uuids, **extra})

    def test_the_rows_reach_the_action_in_the_order_asked(self):
        response = self._batch("rename", ["c", "a"], data={"name": "z"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls, [("rename", ["c", "a"], {"name": "z"})])
        self.assertEqual([row["name"] for row in response.data], ["c", "a"])

    def test_an_action_answering_nothing_is_a_204(self):
        response = self._batch("delete", ["b"])
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.calls, [("delete", ["b"], {})])

    def test_one_row_out_of_reach_refuses_the_lot_and_names_it(self):
        absent = uuid.uuid4()
        response = self._send(
            "post",
            {"action": "delete", "uuids": [str(self.rows.named("a")), str(absent)]},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["uuids"])
        self.assertEqual(len(response.data["uuids"]), 1)
        self.assertIn(str(absent), response.data["uuids"][0])
        self.assertEqual(self.calls, [])

    def test_a_refusal_from_the_action_leaves_nothing_written(self):
        response = self._batch("fail", ["a"])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Group.objects.exists())

    def test_the_data_of_an_action_is_validated(self):
        response = self._batch("rename", ["a"], data={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["data"])
        missing = self._batch("rename", ["a"])
        self.assertEqual(list(missing.data), ["data"])
        self.assertEqual(self.calls, [])

    def test_malformed_requests_name_what_is_wrong(self):
        a = str(self.rows.named("a"))
        for body, field in (
            ({"action": "explode", "uuids": [a]}, "action"),
            ({"uuids": [a]}, "action"),
            (["not", "an", "object"], "action"),
            ({"action": [], "uuids": [a]}, "action"),
            ({"action": {"move": 1}, "uuids": [a]}, "action"),
            ({"action": "delete"}, "uuids"),
            ({"action": "delete", "uuids": []}, "uuids"),
            ({"action": "delete", "uuids": ["nope"]}, "uuids"),
            ({"action": "delete", "uuids": [a, a]}, "uuids"),
            ({"action": "delete", "uuids": [str(uuid.uuid4())] * 1001}, "uuids"),
        ):
            with self.subTest(body=str(body)[:60]):
                response = self._send("post", body)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(list(response.data), [field])
        self.assertEqual(self.calls, [])


class _Reorder(ReorderView):
    permission_classes = []
    rows = None
    calls = None

    def get_queryset(self):
        return self.rows

    def perform_reorder(self, rows, data):
        self.calls.append([row.name for row in rows])


class ReorderViewTests(ApiViewTestCase):
    view_class = _Reorder

    def _reorder(self, *uuids):
        return self._send("post", {"uuids": [str(u) for u in uuids]})

    def test_the_rows_arrive_in_the_order_asked(self):
        response = self._reorder(self.rows.named("c"), self.rows.named("a"))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.calls, [["c", "a"]])

    def test_a_row_out_of_reach_refuses_the_whole_order(self):
        response = self._reorder(self.rows.named("c"), uuid.uuid4())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["uuids"])
        self.assertEqual(self.calls, [])

    def test_a_row_listed_twice_is_refused(self):
        response = self._reorder(self.rows.named("c"), self.rows.named("c"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])


class _ReorderLosingARow(_Reorder):
    """Its service finds one row deleted since the request was read."""

    def perform_reorder(self, rows, data):
        super().perform_reorder(rows, data)
        self.rows.rows.remove(next(r for r in self.rows.rows if r.name == "a"))


class ReorderRaceTests(ApiViewTestCase):
    view_class = _ReorderLosingARow

    def test_a_row_deleted_meanwhile_refuses_the_order(self):
        gone = self.rows.named("a")
        response = self._send("post", {"uuids": [str(self.rows.named("c")), str(gone)]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(list(response.data), ["uuids"])
        self.assertIn(str(gone), response.data["uuids"][0])


class _Actions(AvailableActionsView):
    permission_classes = []
    rows = None
    calls = None

    def get_available_actions(self, uuids):
        return {row.pk: [{"id": "rename"}] for row in self.rows.filter(pk__in=uuids)}


class AvailableActionsViewTests(ApiViewTestCase):
    view_class = _Actions

    def test_every_uuid_asked_comes_back_and_one_out_of_reach_has_no_action(self):
        a, absent = self.rows.named("a"), uuid.uuid4()
        response = self._send("post", {"uuids": [str(a), str(absent)]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {str(a): [{"id": "rename"}], str(absent): []})

    def test_a_malformed_request_is_refused(self):
        for body in ({}, {"uuids": []}, {"uuids": "x"}, {"uuids": ["nope"]}, []):
            with self.subTest(body=body):
                self.assertEqual(self._send("post", body).status_code, 400)


class _StateSerializer(serializers.Serializer):
    state = serializers.ChoiceField(choices=["watching", "muted"])


class _Link(LinkView):
    permission_classes = []
    queryset = Group.objects.all()
    rows = None
    calls = None

    def perform_link(self, group, data):
        self.calls.append(("link", group.name, dict(data)))

    def perform_unlink(self, group):
        self.calls.append(("unlink", group.name))


class _ValuedLink(_Link):
    serializer_class = _StateSerializer


class LinkViewTests(ApiViewTestCase):
    view_class = _Link

    def setUp(self):
        super().setUp()
        self.group = Group.objects.create(name="editors")

    def test_put_sets_the_link_and_delete_removes_it(self):
        self.assertEqual(self._send("put", pk=self.group.pk).status_code, 204)
        self.assertEqual(self._send("delete", pk=self.group.pk).status_code, 204)
        self.assertEqual(self.calls, [("link", "editors", {}), ("unlink", "editors")])

    def test_a_row_out_of_reach_is_a_404_with_a_body(self):
        for method in ("put", "delete"):
            with self.subTest(method=method):
                response = self._send(method, pk=self.group.pk + 1)
                self.assertEqual(response.status_code, 404)
                self.assertIn("detail", response.data)
        self.assertEqual(self.calls, [])

    def test_a_link_carrying_a_value_validates_it(self):
        self.view_class = _ValuedLink
        refused = self._send("put", {"state": "shouting"}, pk=self.group.pk)
        self.assertEqual(refused.status_code, 400)
        accepted = self._send("put", {"state": "muted"}, pk=self.group.pk)
        self.assertEqual(accepted.status_code, 204)
        self.assertEqual(self.calls, [("link", "editors", {"state": "muted"})])
