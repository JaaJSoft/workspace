"""End-to-end content search: global search provider and the list API."""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase
from rest_framework.test import APITestCase

from workspace.common.documents import office
from workspace.common.search import fts5_available
from workspace.common.tests.office_fixtures import (
    make_docx,
    make_odf,
    make_pptx,
    make_xlsx,
)
from workspace.common.tests.pdf_fixtures import make_pdf
from workspace.files.models import File
from workspace.files.search import search_files
from workspace.files.services.search_index import index_file

User = get_user_model()


class ContentSearchTestCase(TestCase):
    def setUp(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        self.user = User.objects.create_user(username="alice", password="pw")
        self.other = User.objects.create_user(username="bob", password="pw")

    def _file(self, owner, name, body=b"", mime="text/markdown"):
        file_obj = File.objects.create(
            owner=owner,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type=mime,
            content=ContentFile(body, name=name) if body else None,
        )
        index_file(file_obj)
        return file_obj


class SearchFilesProviderTests(ContentSearchTestCase):
    def test_a_word_only_in_the_body_finds_the_file(self):
        self._file(self.user, "minutes.md", b"the treasurer resigned")
        results = search_files("treasurer", self.user, limit=10)
        self.assertEqual([r.name for r in results], ["minutes.md"])
        self.assertEqual(results[0].match_type, "content")

    def test_a_plain_text_upload_is_searchable_by_content(self):
        self._file(self.user, "todo.txt", b"renew the passport", mime="text/plain")
        results = search_files("passport", self.user, limit=10)
        self.assertEqual([r.name for r in results], ["todo.txt"])

    def test_a_csv_upload_is_searchable_by_content(self):
        self._file(self.user, "sales.csv", b"region,total\nlisbon,42", mime="text/csv")
        self.assertEqual(
            [r.name for r in search_files("lisbon", self.user, limit=10)], ["sales.csv"]
        )

    def test_an_html_upload_is_searchable_by_its_prose(self):
        self._file(
            self.user,
            "page.html",
            b"<html><body><h1>Onboarding</h1></body></html>",
            mime="text/html",
        )
        self.assertEqual(
            [r.name for r in search_files("onboarding", self.user, limit=10)],
            ["page.html"],
        )

    def test_adjacent_html_blocks_are_searchable_as_separate_words(self):
        # Real markup has no whitespace between block tags; without a boundary
        # the two headings would be indexed as one unsearchable token.
        self._file(
            self.user,
            "handbook.html",
            b"<h1>Onboarding</h1><p>Expenses</p>",
            mime="text/html",
        )
        for term in ("onboarding", "expenses"):
            with self.subTest(term=term):
                self.assertEqual(
                    [r.name for r in search_files(term, self.user, limit=10)],
                    ["handbook.html"],
                )

    def test_name_search_is_accent_insensitive(self):
        self._file(self.user, "Réunion.md")
        results = search_files("reunion", self.user, limit=10)
        self.assertEqual([r.name for r in results], ["Réunion.md"])
        self.assertEqual(results[0].match_type, "name")

    def test_folders_are_still_findable_by_name(self):
        folder = File.objects.create(
            owner=self.user, name="Archives", node_type=File.NodeType.FOLDER
        )
        index_file(folder)
        results = search_files("archives", self.user, limit=10)
        self.assertEqual([r.name for r in results], ["Archives"])
        self.assertEqual(results[0].url, f"/files/{folder.uuid}")

    def test_content_of_another_users_file_never_leaks(self):
        self._file(self.other, "theirs.md", b"the treasurer resigned")
        self.assertEqual(search_files("treasurer", self.user, limit=10), [])

    def test_a_binary_file_is_still_found_by_name(self):
        self._file(self.user, "diagram.png", b"\x89PNG\xff\xfe", mime="image/png")
        self.assertEqual(
            [r.name for r in search_files("diagram", self.user, limit=10)],
            ["diagram.png"],
        )

    def test_a_trashed_file_drops_out_of_search(self):
        note = self._file(self.user, "minutes.md", b"the treasurer resigned")
        note.soft_delete()
        self.assertEqual(search_files("treasurer", self.user, limit=10), [])

    def test_restoring_brings_it_back(self):
        note = self._file(self.user, "minutes.md", b"the treasurer resigned")
        note.soft_delete()
        note.restore()
        self.assertEqual(
            [r.name for r in search_files("treasurer", self.user, limit=10)],
            ["minutes.md"],
        )


class FileListSearchApiTests(APITestCase):
    """The notes page and the file browser both filter through ?search=."""

    def setUp(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        self.user = User.objects.create_user(username="alice", password="pw")
        self.client.force_authenticate(user=self.user)

    def _note(self, name, body=b""):
        note = File.objects.create(
            owner=self.user,
            name=name,
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
            content=ContentFile(body, name=name) if body else None,
        )
        index_file(note)
        return note

    def _names(self, params):
        response = self.client.get("/api/v1/files", params)
        self.assertEqual(response.status_code, 200)
        return [row["name"] for row in response.data]

    def test_search_matches_a_word_only_in_the_body(self):
        self._note("minutes.md", b"the treasurer resigned")
        self._note("unrelated.md", b"nothing to see")
        self.assertEqual(self._names({"search": "treasurer"}), ["minutes.md"])

    def test_search_still_matches_the_name(self):
        self._note("Quarterly report.md", b"body text")
        self.assertEqual(self._names({"search": "quarterly"}), ["Quarterly report.md"])

    def test_results_are_ordered_by_relevance(self):
        self._note("kraken.md", b"unrelated body")
        self._note("zzz.md", b"a passing mention of the kraken")
        # Alphabetically zzz.md would come last; the name hit outranks it.
        self.assertEqual(self._names({"search": "kraken"}), ["kraken.md", "zzz.md"])

    def test_an_explicit_ordering_wins_over_relevance(self):
        self._note("kraken.md", b"unrelated body")
        self._note("aaa.md", b"a passing mention of the kraken")
        self.assertEqual(
            self._names({"search": "kraken", "ordering": "name"}),
            ["aaa.md", "kraken.md"],
        )

    def test_no_search_param_leaves_the_listing_alone(self):
        self._note("root.md", b"body")
        self.assertEqual(self._names({}), ["root.md"])

    def test_a_blank_search_is_not_a_filter(self):
        self._note("root.md", b"body")
        self.assertEqual(self._names({"search": "  "}), ["root.md"])

    def test_search_no_longer_matches_the_file_type(self):
        # `type` left the search document on purpose: ?search=pdf must find
        # what is named or written "pdf", not every PDF in the tree.
        File.objects.create(
            owner=self.user,
            name="invoice.pdf",
            node_type=File.NodeType.FILE,
            mime_type="application/pdf",
            type="pdf",
        )
        self.assertEqual(self._names({"search": "pdf"}), [])

    def test_search_composes_with_the_type_filter(self):
        # Narrowing a search to one file type is ?search=<term>&type=<type>.
        for name, file_type, mime in (
            ("quarterly.pdf", "pdf", "application/pdf"),
            ("quarterly.md", "markdown", "text/markdown"),
        ):
            File.objects.create(
                owner=self.user,
                name=name,
                node_type=File.NodeType.FILE,
                mime_type=mime,
                type=file_type,
                content=ContentFile(b"revenue", name=name),
            )
        index_file(File.objects.get(name="quarterly.pdf"))
        index_file(File.objects.get(name="quarterly.md"))

        self.assertEqual(
            sorted(self._names({"search": "quarterly"})),
            ["quarterly.md", "quarterly.pdf"],
        )
        self.assertEqual(
            self._names({"search": "quarterly", "type": "pdf"}), ["quarterly.pdf"]
        )

    def test_the_trash_listing_can_be_searched_by_content(self):
        # Trashing leaves the document in place, so the trash view - which
        # runs through the same filter backend - can still find it.
        note = self._note("minutes.md", b"the treasurer resigned")
        note.soft_delete()
        response = self.client.get("/api/v1/files/trash", {"search": "treasurer"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["name"] for row in response.data], ["minutes.md"])


class DocumentContentSearchTests(ContentSearchTestCase):
    """The headline case: a word that lives only inside a document."""

    def test_a_word_only_inside_a_pdf_finds_the_file(self):
        self._file(
            self.user,
            "report.pdf",
            make_pdf(["quarterly budget"]),
            mime="application/pdf",
        )
        results = search_files("budget", self.user, limit=10)
        self.assertEqual([r.name for r in results], ["report.pdf"])
        self.assertEqual(results[0].match_type, "content")

    def test_a_word_only_inside_an_office_document_finds_the_file(self):
        documents = (
            ("minutes.docx", office.DOCX, make_docx(["the treasurer resigned"])),
            ("sales.xlsx", office.XLSX, make_xlsx(sheets={"Sales": [["treasurer"]]})),
            ("deck.pptx", office.PPTX, make_pptx([["the treasurer resigned"]])),
            ("notes.odt", office.ODT, make_odf(office.ODT, ["treasurer"])),
            ("budget.ods", office.ODS, make_odf(office.ODS, ["treasurer"])),
            ("slides.odp", office.ODP, make_odf(office.ODP, ["treasurer"])),
        )
        for name, mime, payload in documents:
            self._file(self.user, name, payload, mime=mime)

        results = search_files("treasurer", self.user, limit=10)
        self.assertEqual(
            sorted(r.name for r in results), sorted(name for name, _, _ in documents)
        )
        self.assertTrue(all(r.match_type == "content" for r in results))

    def test_a_scanned_pdf_is_still_findable_by_its_name(self):
        # No text layer means no body; the file must not fall out of search.
        self._file(
            self.user, "invoice-scan.pdf", make_pdf([""]), mime="application/pdf"
        )
        results = search_files("invoice", self.user, limit=10)
        self.assertEqual([r.name for r in results], ["invoice-scan.pdf"])
        self.assertEqual(results[0].match_type, "name")

    def test_another_users_document_stays_out_of_reach(self):
        self._file(
            self.other,
            "private.docx",
            make_docx(["treasurer"]),
            mime=office.DOCX,
        )
        self.assertEqual(search_files("treasurer", self.user, limit=10), [])
