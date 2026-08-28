import json
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from workspace.files.ai_tools import FilesToolProvider, SearchFilenamesParams
from workspace.files.models import File
from workspace.users.services.settings import set_setting

User = get_user_model()


class SearchFilesTimezoneTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tzfiles", password="pw")

    def tearDown(self):
        cache.clear()

    def test_updated_at_rendered_in_user_timezone(self):
        f = File.objects.create(
            owner=self.user,
            name="boundary-report.txt",
            node_type=File.NodeType.FILE,
        )
        # 23:30 UTC on Jan 31 is already Feb 1 in Paris.
        File.objects.filter(pk=f.pk).update(
            updated_at=datetime(2026, 1, 31, 23, 30, tzinfo=UTC)
        )
        set_setting(self.user, "core", "timezone", "Europe/Paris")
        result = FilesToolProvider().search_filenames(
            SearchFilenamesParams(query="boundary-report"),
            user=self.user,
            bot=None,
            conversation_id=None,
            context={},
        )
        payload = json.loads(result)
        self.assertEqual(payload[0]["updated_at"], "2026-02-01 00:30")
