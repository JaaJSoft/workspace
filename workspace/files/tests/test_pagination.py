from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from workspace.common.search import fts5_available
from workspace.common.uuids import uuid_v7_or_v4
from workspace.files.models import File
from workspace.files.services.search_index import index_file


class FileListPaginationTests(TestCase):
    """Opt-in ?limit=/?offset= slicing on the file list endpoint."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="paginator", password="pw"
        )
        cls.root = File.objects.create(
            owner=cls.user, name="Notes", node_type=File.NodeType.FOLDER
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _make_notes(self, count, *, parent=None, same_timestamp=False, prefix="note"):
        parent = parent or self.root
        stamp = timezone.now()
        notes = [
            File(
                uuid=uuid_v7_or_v4(),
                owner=self.user,
                name=f"{prefix}-{i:04d}.md",
                node_type=File.NodeType.FILE,
                parent=parent,
                type="markdown",
                path=f"{parent.path}/{prefix}-{i:04d}.md",
            )
            for i in range(count)
        ]
        created = File.objects.bulk_create(notes)
        if same_timestamp:
            File.objects.filter(pk__in=[n.pk for n in created]).update(
                updated_at=stamp, created_at=stamp
            )
        return created

    def _url(self, **params):
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        return f"/api/v1/files?type=markdown&parent={self.root.uuid}&{query}"

    # ── Opt-in ────────────────────────────────────────────────

    def test_without_limit_the_full_list_is_returned(self):
        self._make_notes(12)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 12)
        self.assertNotIn("X-Has-More", resp)

    def test_limit_slices_the_list_and_keeps_a_bare_array(self):
        self._make_notes(12)
        resp = self.client.get(self._url(limit=5))
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsInstance(body, list)
        self.assertEqual(len(body), 5)
        self.assertEqual(resp["X-Has-More"], "true")

    def test_offset_shifts_the_window_and_the_last_page_reports_no_more(self):
        self._make_notes(12)
        resp = self.client.get(self._url(limit=5, offset=10))
        self.assertEqual(len(resp.json()), 2)
        self.assertEqual(resp["X-Has-More"], "false")

    # ── Ordering stability ────────────────────────────────────

    def test_paging_covers_every_row_exactly_once_on_tied_timestamps(self):
        """250 notes sharing one updated_at must still page without holes.

        Without a deterministic tiebreaker the database is free to return tied
        rows in a different order per query, so a row can shift between pages
        and never be seen.
        """
        self._make_notes(250, same_timestamp=True)

        seen = []
        offset = 0
        while True:
            resp = self.client.get(
                self._url(limit=50, offset=offset, ordering="-updated_at")
            )
            self.assertEqual(resp.status_code, 200)
            page = resp.json()
            seen.extend(n["uuid"] for n in page)
            if resp["X-Has-More"] != "true":
                break
            offset += 50
            self.assertLess(offset, 1000, "pagination did not terminate")

        self.assertEqual(len(seen), 250, "some rows were skipped or duplicated")
        self.assertEqual(len(set(seen)), 250, "a row was returned on two pages")

    def test_paged_queries_order_by_a_unique_column_last(self):
        """The ORDER BY must end on the pk when paging.

        The behavioural test above cannot catch a missing tiebreaker: SQLite
        happens to return tied rows in rowid order, so it stays green either
        way, and CI runs on SQLite. Only the emitted SQL shows the invariant.
        """
        self._make_notes(3)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(self._url(limit=2, ordering="-updated_at"))
        selects = [
            q["sql"]
            for q in ctx.captured_queries
            if "files_file" in q["sql"] and "ORDER BY" in q["sql"]
        ]
        self.assertTrue(selects, "no ordered query against files_file was issued")
        order_by = selects[-1].rsplit("ORDER BY", 1)[1]
        self.assertIn("uuid", order_by)

    def test_unpaginated_queries_keep_their_ordering_untouched(self):
        self._make_notes(3)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(self._url(ordering="-updated_at"))
        selects = [
            q["sql"]
            for q in ctx.captured_queries
            if "files_file" in q["sql"] and "ORDER BY" in q["sql"]
        ]
        self.assertTrue(selects)
        self.assertNotIn("uuid", selects[-1].rsplit("ORDER BY", 1)[1])

    def test_pages_are_stable_across_identical_requests(self):
        self._make_notes(120, same_timestamp=True)
        url = self._url(limit=40, offset=40, ordering="-updated_at")
        first = [n["uuid"] for n in self.client.get(url).json()]
        second = [n["uuid"] for n in self.client.get(url).json()]
        self.assertEqual(first, second)

    def test_explicit_ordering_is_still_honoured_when_paging(self):
        self._make_notes(10)
        resp = self.client.get(self._url(limit=4, ordering="name"))
        names = [n["name"] for n in resp.json()]
        self.assertEqual(names, sorted(names))
        self.assertEqual(names[0], "note-0000.md")

    # ── Composition with the existing filters ─────────────────

    def test_paging_composes_with_descendants(self):
        child = File.objects.create(
            owner=self.user,
            name="Sub",
            node_type=File.NodeType.FOLDER,
            parent=self.root,
        )
        self._make_notes(6)
        self._make_notes(6, parent=child, prefix="deep")

        resp = self.client.get(self._url(limit=10, descendants=1))
        self.assertEqual(len(resp.json()), 10)
        self.assertEqual(resp["X-Has-More"], "true")

        resp = self.client.get(self._url(limit=10, offset=10, descendants=1))
        self.assertEqual(len(resp.json()), 2)
        self.assertEqual(resp["X-Has-More"], "false")

    def test_paging_composes_with_search(self):
        if connection.vendor != "sqlite" or not fts5_available():
            self.skipTest("SQLite + FTS5 required")
        for note in self._make_notes(5, prefix="alpha"):
            index_file(note)
        for note in self._make_notes(5, prefix="beta"):
            index_file(note)
        resp = self.client.get(self._url(limit=3, search="alpha"))
        body = resp.json()
        self.assertEqual(len(body), 3)
        self.assertTrue(all("alpha" in n["name"] for n in body))
        self.assertEqual(resp["X-Has-More"], "true")

    # ── Precedence over recent_limit ──────────────────────────

    def test_limit_takes_precedence_over_recent_limit(self):
        self._make_notes(40)
        resp = self.client.get(
            "/api/v1/files?type=markdown&recent=1&recent_limit=5&limit=20"
        )
        self.assertEqual(len(resp.json()), 20)

    def test_recent_limit_still_caps_when_no_limit_is_given(self):
        self._make_notes(40)
        resp = self.client.get("/api/v1/files?type=markdown&recent=1&recent_limit=5")
        self.assertEqual(len(resp.json()), 5)
