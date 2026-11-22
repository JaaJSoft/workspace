"""The priority the tasks queued for a written file go out at.

An upload is followed by its dispatch task, which queues the pipeline and the
search document, all at normal priority: someone is waiting for the thumbnail.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.common.task_priority import NORMAL_PRIORITY
from workspace.common.tests.task_queue import queued_tasks
from workspace.files.models import File
from workspace.files.services import tree
from workspace.files.services.content_writes import update_content

User = get_user_model()

DISPATCH = "files.run_file_event_handlers"


def files_tasks(sent):
    # A request also sends tasks of its own (presence), which are not the file's.
    return [task for task in sent if task[0].startswith("files.")]


class UploadFollowUpPriorityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.client.force_authenticate(user=self.user)

    def test_an_upload_queues_its_tasks_at_normal_priority(self):
        with (
            queued_tasks(run={DISPATCH}) as sent,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                "/api/v1/files",
                {
                    "name": "note.txt",
                    "node_type": "file",
                    "content": ContentFile(b"hello", name="note.txt"),
                },
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertCountEqual(
            files_tasks(sent),
            [
                (DISPATCH, NORMAL_PRIORITY),
                ("files.process_file", NORMAL_PRIORITY),
                ("files.index_search_document", NORMAL_PRIORITY),
            ],
        )

    def test_a_new_folder_queues_its_search_document_at_normal_priority(self):
        with (
            queued_tasks(run={DISPATCH}) as sent,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.post(
                "/api/v1/files",
                {"name": "Docs", "node_type": "folder"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertCountEqual(
            files_tasks(sent),
            [
                (DISPATCH, NORMAL_PRIORITY),
                ("files.index_search_document", NORMAL_PRIORITY),
            ],
        )

    def test_a_content_replacement_queues_its_tasks_at_normal_priority(self):
        file_obj = tree.create_file(
            self.user, "note.txt", content=ContentFile(b"one", name="note.txt")
        )

        with (
            queued_tasks(run={DISPATCH}) as sent,
            self.captureOnCommitCallbacks(execute=True),
        ):
            update_content(
                File.objects.get(pk=file_obj.pk),
                ContentFile(b"two", name="note.txt"),
                acting_user=self.user,
            )

        self.assertCountEqual(
            files_tasks(sent),
            [
                (DISPATCH, NORMAL_PRIORITY),
                ("files.process_file", NORMAL_PRIORITY),
                ("files.index_search_document", NORMAL_PRIORITY),
            ],
        )
