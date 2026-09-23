import json
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase

from workspace.files.ai_tools import FilesToolProvider, SearchFilenamesParams
from workspace.files.models import File, FileShare
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


class SearchFilenamesScopeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pw")
        self.bob = User.objects.create_user(username="bob", password="pw")
        self.team = Group.objects.create(name="Team")
        self.user.groups.add(self.team)

    def tearDown(self):
        cache.clear()

    def _search(self, query):
        return json.loads(
            FilesToolProvider().search_filenames(
                SearchFilenamesParams(query=query),
                user=self.user,
                bot=None,
                conversation_id=None,
                context={},
            )
        )

    def test_group_folder_files_are_found(self):
        root = File.objects.create(
            owner=self.bob,
            name="Team files",
            node_type=File.NodeType.FOLDER,
            group=self.team,
        )
        File.objects.create(
            owner=self.bob,
            name="budget.xlsx",
            node_type=File.NodeType.FILE,
            group=self.team,
            parent=root,
        )
        [hit] = self._search("budget")
        self.assertEqual(hit["parent_folder"], "Team files")
        self.assertFalse(hit["shared_with_me"])

    def test_a_file_shared_with_the_user_is_found_without_its_owners_folder(self):
        folder = File.objects.create(
            owner=self.bob, name="Bob private", node_type=File.NodeType.FOLDER
        )
        doc = File.objects.create(
            owner=self.bob,
            name="budget.xlsx",
            node_type=File.NodeType.FILE,
            parent=folder,
        )
        FileShare.objects.create(file=doc, shared_by=self.bob, shared_with=self.user)
        [hit] = self._search("budget")
        self.assertEqual(hit["uuid"], str(doc.uuid))
        self.assertEqual(hit["parent_folder"], "")
        self.assertTrue(hit["shared_with_me"])

    def test_a_group_file_under_a_private_folder_does_not_name_it(self):
        folder = File.objects.create(
            owner=self.bob, name="Bob private", node_type=File.NodeType.FOLDER
        )
        File.objects.create(
            owner=self.bob,
            name="budget.xlsx",
            node_type=File.NodeType.FILE,
            group=self.team,
            parent=folder,
        )
        [hit] = self._search("budget")
        self.assertEqual(hit["parent_folder"], "")
        self.assertTrue(hit["shared_with_me"])

    def test_another_users_unshared_file_stays_hidden(self):
        File.objects.create(
            owner=self.bob, name="budget.xlsx", node_type=File.NodeType.FILE
        )
        self.assertEqual(
            FilesToolProvider().search_filenames(
                SearchFilenamesParams(query="budget"),
                user=self.user,
                bot=None,
                conversation_id=None,
                context={},
            ),
            'No files found matching "budget".',
        )


class ReadFileToolTests(TestCase):
    """What the assistant is handed for a file it has just found."""

    def setUp(self):
        self.user = User.objects.create_user(username="reader", password="pw")

    def _read(self, name, mime, payload):
        from django.core.files.base import ContentFile

        from workspace.files.ai_tools import ReadFileParams

        file_obj = File.objects.create(
            owner=self.user,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type=mime,
            size=len(payload),
            content=ContentFile(payload, name=name),
        )
        return FilesToolProvider().read_file(
            ReadFileParams(uuid=file_obj.uuid),
            user=self.user,
            bot=None,
            conversation_id=None,
            context={},
        )

    def test_a_document_comes_back_as_its_prose(self):
        from workspace.common.documents import extraction
        from workspace.common.tests.office_fixtures import make_docx

        result = self._read(
            "minutes.docx", extraction.DOCX, make_docx(["the treasurer resigned"])
        )
        self.assertIn("minutes.docx", result)
        self.assertIn("treasurer", result)

    def test_a_pdf_is_read_rather_than_handed_over_as_its_source(self):
        # An uncompressed PDF decodes as UTF-8, so a reader that only decoded
        # would hand the model "%PDF-1.4 ... /Type /Catalog" and call it text.
        from workspace.common.tests.pdf_fixtures import make_pdf

        result = self._read(
            "report.pdf", "application/pdf", make_pdf(["quarterly budget"])
        )
        self.assertIn("quarterly budget", result)
        self.assertNotIn("/Type /Catalog", result)

    def test_a_scan_says_it_has_nothing_to_show(self):
        from workspace.common.tests.pdf_fixtures import make_pdf

        result = self._read("scan.pdf", "application/pdf", make_pdf([""]))
        self.assertIn("Cannot read", result)
