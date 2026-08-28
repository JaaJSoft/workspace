from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from workspace.files.services import FileService

User = get_user_model()


class PropertiesPanelQueryCountTests(TestCase):
    """The panel prints the owner, so the owner must ride along with the
    file row instead of being resolved lazily during rendering."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="props_owner", email="props@test.com", password="x"
        )
        self.file = FileService.create_file(
            self.owner, "doc.txt", acting_user=self.owner
        )
        self.client.force_login(self.owner)

    def test_owner_is_joined_not_fetched_during_render(self):
        url = reverse("files_ui:properties", kwargs={"uuid": self.file.uuid})

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        # 1 = the request's own auth lookup. A second one means the template
        # resolved file.owner on its own.
        owner_queries = [
            q
            for q in ctx.captured_queries
            if 'FROM "auth_user"' in q["sql"] and "INNER JOIN" not in q["sql"]
        ]
        self.assertEqual(
            len(owner_queries),
            1,
            f"expected the owner to be select_related, got {len(owner_queries)} "
            f"standalone user queries",
        )
