from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase

from workspace.files.models import FileEvent, FileLink
from workspace.files.services import FileService
from workspace.files.services.link_events import update_file_links_for_event

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


class LinkEventHandlerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="evt-links", password="p")

    def _event(self, file, action=FileEvent.Action.CONTENT_REPLACED):
        return FileEvent.objects.create(file=file, actor=self.user, action=action)

    def test_handler_reconciles_on_content_replaced(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        update_file_links_for_event(self._event(a))
        self.assertEqual(
            set(FileLink.objects.filter(source=a).values_list("target_id", flat=True)),
            {b.uuid},
        )

    def test_handler_reconciles_on_created(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        update_file_links_for_event(self._event(a, FileEvent.Action.CREATED))
        self.assertEqual(FileLink.objects.filter(source=a, target=b).count(), 1)

    def test_trashed_file_is_skipped(self):
        b = _make_markdown(self.user, "B.md", "# B")
        a = _make_markdown(self.user, "A.md", f"[B](/notes?file={b.uuid})")
        FileService.soft_delete(a, acting_user=self.user)
        a.refresh_from_db()
        update_file_links_for_event(self._event(a))
        self.assertEqual(FileLink.objects.filter(source=a).count(), 0)
