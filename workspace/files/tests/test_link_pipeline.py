from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import FileLink
from workspace.files.services import FileService
from workspace.files.services.processors import run_pipeline

User = get_user_model()


def _make_markdown(user, name, body=""):
    f = FileService.create_file(
        owner=user,
        name=name,
        content=ContentFile(body.encode("utf-8"), name=name),
        mime_type="text/markdown",
    )
    f.type = "markdown"
    f.save(update_fields=["type"])
    return f


class LinkPipelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="evt-links", password="p")

    def test_pipeline_reconciles_a_note(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        run_pipeline(a.uuid)
        self.assertEqual(
            set(FileLink.objects.filter(source=a).values_list("target_id", flat=True)),
            {b.uuid},
        )

    def test_pipeline_reconciles_a_new_note(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        run_pipeline(a.uuid)
        self.assertEqual(FileLink.objects.filter(source=a, target=b).count(), 1)

    def test_trashed_file_is_skipped(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        FileService.soft_delete(a, acting_user=self.user)
        a.refresh_from_db()
        run_pipeline(a.uuid)
        self.assertEqual(FileLink.objects.filter(source=a).count(), 0)
