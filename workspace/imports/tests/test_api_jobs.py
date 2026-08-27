from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from workspace.imports.models import ImportConnection, ImportJob, ImportJobItem
from workspace.imports.services import jobs as svc

from .fakes import fake_provider

User = get_user_model()
BASE = "/api/v1/imports"


@override_settings(IMPORTS_ALLOWED_HOSTS=["x", "y"])
class JobsApiTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")
        self.provider = fake_provider()
        self.conn = ImportConnection.objects.create(
            owner=self.user,
            provider="fake",
            label="Cloud",
            base_url="https://x/dav",
            username="a",
        )
        self.theirs = ImportConnection.objects.create(
            owner=self.other,
            provider="fake",
            label="Theirs",
            base_url="https://x/dav",
            username="b",
        )
        self.client.force_authenticate(self.user)
        enqueue_patch = patch("workspace.imports.services.jobs._enqueue")
        self.enqueue = enqueue_patch.start()
        self.addCleanup(enqueue_patch.stop)

    def tearDown(self):
        cache.clear()

    def _create(self, **overrides):
        payload = {
            "connection": str(self.conn.uuid),
            "kinds": ["files"],
            "options": {"files": {"on_conflict": "skip"}},
        }
        payload.update(overrides)
        return self.client.post(f"{BASE}/jobs", payload, format="json")

    def test_create_returns_the_pending_job(self):
        response = self._create()
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()
        self.assertEqual(data["status"], "pending")
        self.assertEqual(data["connection"], str(self.conn.uuid))
        self.assertEqual(data["connection_label"], "Cloud")
        self.assertEqual(data["options"]["files"]["on_conflict"], "skip")
        self.assertEqual(data["kinds"], ["files"])

    def test_create_rejects_someone_elses_connection(self):
        response = self._create(connection=str(self.theirs.uuid))
        self.assertEqual(response.status_code, 400)
        self.assertIn("connection", response.json())

    def test_create_surfaces_option_errors_per_kind(self):
        response = self._create(options={"files": {"on_conflict": "nope"}})
        self.assertEqual(response.status_code, 400)
        self.assertIn("on_conflict", response.json()["options"]["files"])

    def test_create_rejects_unknown_kind(self):
        response = self._create(kinds=["photos"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("photos", response.json()["detail"])

    def test_create_is_a_409_while_another_job_runs(self):
        ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.RUNNING
        )
        self.assertEqual(self._create().status_code, 409)

    def test_list_and_detail_are_owner_scoped(self):
        mine = ImportJob.objects.create(connection=self.conn, kinds=["files"])
        theirs = ImportJob.objects.create(connection=self.theirs, kinds=["files"])
        listed = self.client.get(f"{BASE}/jobs").json()
        self.assertEqual(listed["count"], 1)
        self.assertEqual([j["uuid"] for j in listed["results"]], [str(mine.uuid)])
        self.assertEqual(self.client.get(f"{BASE}/jobs/{mine.uuid}").status_code, 200)
        self.assertEqual(self.client.get(f"{BASE}/jobs/{theirs.uuid}").status_code, 404)
        self.assertEqual(
            self.client.post(f"{BASE}/jobs/{theirs.uuid}/cancel").status_code, 404
        )

    def test_jobs_list_is_paged_newest_first(self):
        first = ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.COMPLETED
        )
        second = ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.FAILED
        )
        response = self.client.get(f"{BASE}/jobs", {"limit": 1})
        self.assertEqual(response.json()["count"], 2)
        self.assertEqual(
            [j["uuid"] for j in response.json()["results"]], [str(second.uuid)]
        )
        response = self.client.get(f"{BASE}/jobs", {"limit": 1, "offset": 1})
        self.assertEqual(
            [j["uuid"] for j in response.json()["results"]], [str(first.uuid)]
        )

    def test_items_can_be_filtered_and_paged(self):
        job = ImportJob.objects.create(
            connection=self.conn, kinds=["files"], status=ImportJob.Status.COMPLETED
        )
        for i in range(3):
            ImportJobItem.objects.create(
                job=job,
                kind="files",
                remote_id=f"/ok{i}",
                status=ImportJobItem.Status.DONE,
            )
        ImportJobItem.objects.create(
            job=job,
            kind="files",
            remote_id="/bad",
            status=ImportJobItem.Status.FAILED,
            error="gone",
        )

        response = self.client.get(
            f"{BASE}/jobs/{job.uuid}/items", {"status": "failed"}
        )
        self.assertEqual(response.json()["count"], 1)
        self.assertEqual(response.json()["results"][0]["remote_id"], "/bad")
        self.assertEqual(response.json()["results"][0]["error"], "gone")

        response = self.client.get(
            f"{BASE}/jobs/{job.uuid}/items", {"limit": 2, "offset": 2}
        )
        self.assertEqual(response.json()["count"], 4)
        self.assertEqual(len(response.json()["results"]), 2)

    def test_cancel(self):
        job = ImportJob.objects.create(connection=self.conn, kinds=["files"])
        response = self.client.post(f"{BASE}/jobs/{job.uuid}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        self.assertEqual(
            self.client.post(f"{BASE}/jobs/{job.uuid}/cancel").status_code, 400
        )

    def test_retry(self):
        failed = ImportJob.objects.create(
            connection=self.conn,
            kinds=["files"],
            status=ImportJob.Status.FAILED,
            options={"files": {}},
        )
        response = self.client.post(f"{BASE}/jobs/{failed.uuid}/retry")
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotEqual(response.json()["uuid"], str(failed.uuid))
        self.assertEqual(response.json()["status"], "pending")
        # the new job blocks a second retry
        self.assertEqual(
            self.client.post(f"{BASE}/jobs/{failed.uuid}/retry").status_code, 409
        )

    def test_end_to_end_through_the_api_in_eager_mode(self):
        # Nested patch of the same target overrides setUp's mock for this block.
        with patch(
            "workspace.imports.services.jobs._enqueue",
            side_effect=lambda job: svc.run_job(job.pk),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self._create()
        job = self.client.get(f"{BASE}/jobs/{response.json()['uuid']}").json()
        self.assertEqual(job["status"], "completed")
        self.assertEqual(job["stats"]["files"]["files"], 3)
