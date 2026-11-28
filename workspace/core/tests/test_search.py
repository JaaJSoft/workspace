"""Tests for search functions across all modules."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from workspace.common.tests.slow_queries import run_endless_query
from workspace.core.module_registry import (
    CommandInfo,
    ModuleInfo,
    ModuleRegistry,
    SearchAnswer,
    SearchProviderInfo,
    SearchResult,
)

User = get_user_model()


def _result(name, module_slug):
    return SearchResult(
        uuid=name,
        name=name,
        url="/",
        matched_value=name,
        match_type="title",
        type_icon="i",
        module_slug=module_slug,
    )


def _module(slug, preview=False, active=True):
    return ModuleInfo(
        name=slug.title(),
        slug=slug,
        description="",
        icon="i",
        color="indigo",
        url=f"/{slug}",
        active=active,
        preview=preview,
    )


# ── Chat search ─────────────────────────────────────────────────


class ChatSearchTests(TestCase):
    def setUp(self):
        from workspace.chat.models import Conversation, ConversationMember

        self.alice = User.objects.create_user(
            username="alice",
            password="pass",
            first_name="Alice",
            last_name="Smith",
        )
        self.bob = User.objects.create_user(
            username="bob",
            password="pass",
            first_name="Bob",
            last_name="Jones",
        )
        # Group conversation
        self.group = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Engineering",
            created_by=self.alice,
        )
        ConversationMember.objects.create(conversation=self.group, user=self.alice)
        # DM conversation
        self.dm = Conversation.objects.create(
            kind=Conversation.Kind.DM,
            created_by=self.alice,
        )
        ConversationMember.objects.create(conversation=self.dm, user=self.alice)
        ConversationMember.objects.create(conversation=self.dm, user=self.bob)

    def test_search_group_by_title(self):
        from workspace.chat.search import search_conversations

        results = search_conversations("Engineer", self.alice, 10)
        names = [r.name for r in results]
        self.assertIn("Engineering", names)

    def test_search_dm_by_member_name(self):
        from workspace.chat.search import search_conversations

        results = search_conversations("Bob", self.alice, 10)
        self.assertGreaterEqual(len(results), 1)

    def test_excludes_non_member_conversations(self):
        from workspace.chat.models import Conversation, ConversationMember
        from workspace.chat.search import search_conversations

        carol = User.objects.create_user(username="carol", password="pass")
        secret = Conversation.objects.create(
            kind=Conversation.Kind.GROUP,
            title="Secret",
            created_by=carol,
        )
        ConversationMember.objects.create(conversation=secret, user=carol)
        results = search_conversations("Secret", self.alice, 10)
        self.assertEqual(len(results), 0)

    def test_no_results_for_unmatched_query(self):
        from workspace.chat.search import search_conversations

        results = search_conversations("zzzznonexistent", self.alice, 10)
        self.assertEqual(len(results), 0)


# ── Files search ────────────────────────────────────────────────


class FilesSearchTests(TestCase):
    """File search reads the full-text index, which the indexing task writes.

    Celery is not eager under test, so each fixture indexes itself - the same
    thing the hourly catch-up does for an existing install.
    """

    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="pass")
        self.f1 = self._file("report.pdf")
        self.folder = self._file("Documents", folder=True)

    def _file(self, name, *, owner=None, parent=None, folder=False):
        from workspace.files.models import File
        from workspace.files.services.search_index import index_file

        file_obj = File.objects.create(
            owner=owner or self.alice,
            name=name,
            parent=parent,
            node_type=File.NodeType.FOLDER if folder else File.NodeType.FILE,
        )
        index_file(file_obj)
        return file_obj

    def test_search_by_filename(self):
        from workspace.files.search import search_files

        results = search_files("report", self.alice, 10)
        names = [r.name for r in results]
        self.assertIn("report.pdf", names)

    def test_search_finds_folders(self):
        from workspace.files.search import search_files

        results = search_files("Documents", self.alice, 10)
        self.assertGreaterEqual(len(results), 1)

    def test_file_in_folder_url_opens_folder_and_viewer(self):
        from workspace.files.search import search_files

        nested = self._file("nested.pdf", parent=self.folder)
        result = next(r for r in search_files("nested", self.alice, 10))
        # Path lands in the parent folder, ?open= triggers the file viewer.
        self.assertEqual(result.url, f"/files/{self.folder.uuid}?open={nested.uuid}")

    def test_root_file_url_opens_viewer(self):
        from workspace.files.search import search_files

        result = next(r for r in search_files("report", self.alice, 10))
        self.assertEqual(result.url, f"/files?open={self.f1.uuid}")

    def test_folder_url_has_no_open_param(self):
        from workspace.files.search import search_files

        result = next(r for r in search_files("Documents", self.alice, 10))
        self.assertEqual(result.url, f"/files/{self.folder.uuid}")

    def test_excludes_other_users_files(self):
        from workspace.files.search import search_files

        bob = User.objects.create_user(username="bob", password="pass")
        self._file("secret.txt", owner=bob)
        results = search_files("secret", self.alice, 10)
        self.assertEqual(len(results), 0)

    def test_no_results_for_unmatched(self):
        from workspace.files.search import search_files

        results = search_files("zzzznonexistent", self.alice, 10)
        self.assertEqual(len(results), 0)


# ── Notes search ────────────────────────────────────────────────


class NotesSearchTests(TestCase):
    def setUp(self):
        from workspace.files.models import File
        from workspace.files.services.search_index import index_file

        self.alice = User.objects.create_user(username="alice", password="pass")
        self.note = File.objects.create(
            owner=self.alice,
            name="meeting-notes.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )
        self.non_note = File.objects.create(
            owner=self.alice,
            name="meeting-notes.pdf",
            node_type=File.NodeType.FILE,
            mime_type="application/pdf",
        )
        index_file(self.note)
        index_file(self.non_note)

    def test_finds_markdown_files(self):
        from workspace.notes.search import search_notes

        results = search_notes("meeting", self.alice, 10)
        names = [r.name for r in results]
        self.assertIn("meeting-notes.md", names)

    def test_excludes_non_markdown(self):
        from workspace.notes.search import search_notes

        results = search_notes("meeting", self.alice, 10)
        names = [r.name for r in results]
        self.assertNotIn("meeting-notes.pdf", names)

    def test_no_results_for_unmatched(self):
        from workspace.notes.search import search_notes

        results = search_notes("zzzznonexistent", self.alice, 10)
        self.assertEqual(len(results), 0)


# ── Calendar search ─────────────────────────────────────────────


class CalendarEventSearchTests(TestCase):
    def setUp(self):
        from workspace.calendar.models import Calendar, Event

        self.alice = User.objects.create_user(username="alice", password="pass")
        self.cal = Calendar.objects.create(name="Work", owner=self.alice)
        self.event = Event.objects.create(
            calendar=self.cal,
            owner=self.alice,
            title="Sprint Planning",
            start=timezone.now(),
        )

    def test_search_by_title(self):
        from workspace.calendar.search import search_events

        results = search_events("Sprint", self.alice, 10)
        names = [r.name for r in results]
        self.assertIn("Sprint Planning", names)

    def test_excludes_cancelled(self):
        from workspace.calendar.search import search_events

        self.event.is_cancelled = True
        self.event.save()
        results = search_events("Sprint", self.alice, 10)
        self.assertEqual(len(results), 0)

    def test_excludes_other_users_events(self):
        from workspace.calendar.models import Calendar, Event
        from workspace.calendar.search import search_events

        bob = User.objects.create_user(username="bob", password="pass")
        bob_cal = Calendar.objects.create(name="Bob", owner=bob)
        Event.objects.create(
            calendar=bob_cal,
            owner=bob,
            title="Secret meeting",
            start=timezone.now(),
        )
        results = search_events("Secret", self.alice, 10)
        self.assertEqual(len(results), 0)


class CalendarPollSearchTests(TestCase):
    def setUp(self):
        from workspace.calendar.models import Poll

        self.alice = User.objects.create_user(username="alice", password="pass")
        self.poll = Poll.objects.create(
            title="Team Lunch Date",
            created_by=self.alice,
        )

    def test_search_by_title(self):
        from workspace.calendar.search import search_polls

        results = search_polls("Lunch", self.alice, 10)
        names = [r.name for r in results]
        self.assertIn("Team Lunch Date", names)

    def test_excludes_other_users_polls(self):
        from workspace.calendar.search import search_polls

        bob = User.objects.create_user(username="bob", password="pass")
        results = search_polls("Lunch", bob, 10)
        self.assertEqual(len(results), 0)


# ── Mail search ─────────────────────────────────────────────────


class MailSearchTests(TestCase):
    def setUp(self):
        from workspace.mail.models import MailAccount, MailFolder, MailMessage

        self.alice = User.objects.create_user(username="alice", password="pass")
        self.account = MailAccount.objects.create(
            owner=self.alice,
            email="alice@example.com",
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="alice",
        )
        self.folder = MailFolder.objects.create(
            account=self.account,
            name="INBOX",
            display_name="Inbox",
        )
        self.msg = MailMessage.objects.create(
            account=self.account,
            folder=self.folder,
            imap_uid=1,
            subject="Invoice #1234",
            snippet="Please pay",
            from_name="Billing",
            from_email="billing@corp.com",
            date=timezone.now(),
        )

    def test_search_by_subject(self):
        from workspace.mail.search import search_mail

        results = search_mail("Invoice", self.alice, 10)
        names = [r.name for r in results]
        self.assertIn("Invoice #1234", names)

    def test_search_by_from_address(self):
        from workspace.mail.search import search_mail

        results = search_mail("billing", self.alice, 10)
        self.assertGreaterEqual(len(results), 1)

    def test_excludes_other_users_mail(self):
        from workspace.mail.search import search_mail

        bob = User.objects.create_user(username="bob", password="pass")
        results = search_mail("Invoice", bob, 10)
        self.assertEqual(len(results), 0)

    def test_excludes_hidden_folders(self):
        from workspace.mail.models import MailFolder, MailMessage
        from workspace.mail.search import search_mail

        hidden = MailFolder.objects.create(
            account=self.account,
            name="Hidden",
            display_name="Hidden",
            is_hidden=True,
        )
        MailMessage.objects.create(
            account=self.account,
            folder=hidden,
            imap_uid=2,
            subject="Hidden Invoice",
            date=timezone.now(),
        )
        results = search_mail("Hidden Invoice", self.alice, 10)
        self.assertEqual(len(results), 0)

    def test_excludes_deleted_messages(self):
        from workspace.mail.search import search_mail

        self.msg.deleted_at = timezone.now()
        self.msg.save()
        results = search_mail("Invoice", self.alice, 10)
        self.assertEqual(len(results), 0)


# -- Unified search visibility -------------------------------------------


class UnifiedSearchVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="se", password="x")
        self.client.force_login(self.user)

    def tearDown(self):
        # UnifiedSearchView is wrapped in @cached_response; LocMemCache is
        # process-global across TestCase runs, so clear it between tests.
        cache.clear()

    @override_settings(PREVIEW_VISIBILITY="none")
    @patch("workspace.core.services.module_visibility.registry")
    @patch("workspace.core.services.search.registry")
    def test_hidden_module_results_and_commands_filtered(
        self, mock_registry, mock_visibility_registry
    ):
        mock_registry.refinements.return_value = {}
        mock_registry.search.return_value = SearchAnswer(
            hits=[
                {
                    "uuid": "u1",
                    "provider_slug": "files",
                    "module_slug": "files",
                    "name": "doc",
                },
                {
                    "uuid": "u2",
                    "provider_slug": "lab",
                    "module_slug": "lab",
                    "name": "secret",
                },
            ]
        )
        mock_registry.search_commands.return_value = [
            CommandInfo(
                name="Lab",
                keywords=[],
                icon="i",
                url="/lab",
                kind="navigate",
                module_slug="lab",
            ),
        ]
        mock_visibility_registry.get.side_effect = lambda slug: _module(
            slug, preview=slug == "lab"
        )

        resp = self.client.get("/api/v1/search?q=do")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        result_slugs = {r["module_slug"] for r in body["results"]}
        self.assertEqual(result_slugs, {"files"})
        self.assertEqual(body["commands"], [])
        self.assertEqual(body["count"], 1)


# ── Cross-provider deduplication ────────────────────────────────


class MarkdownNoteDeduplicationTests(TestCase):
    """A markdown note is a File row, so `files` and `notes` both answer with it."""

    def setUp(self):
        from workspace.files.models import File
        from workspace.files.services.search_index import index_file

        self.alice = User.objects.create_user(username="alice", password="pass")
        self.note = File.objects.create(
            owner=self.alice,
            name="meeting-notes.md",
            node_type=File.NodeType.FILE,
            mime_type="text/markdown",
        )
        self.report = File.objects.create(
            owner=self.alice,
            name="meeting-report.pdf",
            node_type=File.NodeType.FILE,
            mime_type="application/pdf",
        )
        index_file(self.note)
        index_file(self.report)

    def _hits_for(self, uuid):
        from workspace.core.services.search import search_modules

        hits = search_modules("meeting", self.alice).hits
        return [h for h in hits if h["uuid"] == uuid]

    def test_a_note_is_returned_once_pointing_at_the_editor(self):
        hits = self._hits_for(str(self.note.uuid))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["url"], f"/notes?file={self.note.uuid}")

    def test_a_non_markdown_file_still_points_at_the_file_viewer(self):
        hits = self._hits_for(str(self.report.uuid))
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["module_slug"], "files")

    @patch("workspace.core.services.search.is_module_slug_visible")
    def test_a_user_without_the_notes_module_still_gets_the_file_row(
        self, mock_visible
    ):
        mock_visible.side_effect = lambda user, slug: slug != "notes"

        hits = self._hits_for(str(self.note.uuid))

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["module_slug"], "files")


# ── Providers past their budget ─────────────────────────────────


class UnifiedSearchPartialAnswerTests(TestCase):
    """A provider past its budget leaves the answer partial, and no cache
    keeps a partial answer: the next request asks the providers again."""

    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="pass")
        self.client.force_login(self.user)
        self.files_is_slow = True

        def search_files(query, user, limit):
            if self.files_is_slow:
                run_endless_query()
            return [_result("late", "files")]

        reg = ModuleRegistry()
        reg.register(_module("files"))
        reg.register(_module("chat"))
        reg.register_search_provider(
            SearchProviderInfo(
                slug="files", module_slug="files", search_fn=search_files
            )
        )
        reg.register_search_provider(
            SearchProviderInfo(
                slug="chat",
                module_slug="chat",
                search_fn=lambda q, u, limit: [_result("on time", "chat")],
            )
        )
        for patcher in (
            patch("workspace.core.services.search.registry", reg),
            patch("workspace.core.services.search.PROVIDER_BUDGET_SECONDS", 0.2),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self):
        cache.clear()

    def _names(self, response):
        return [r["name"] for r in response.json()["results"]]

    def test_the_answer_leaves_the_slow_provider_out(self):
        resp = self.client.get("/api/v1/search?q=report")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._names(resp), ["on time"])
        self.assertTrue(resp.json()["is_partial"])
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_a_partial_answer_is_not_cached(self):
        self.client.get("/api/v1/search?q=report")
        self.files_is_slow = False

        resp = self.client.get("/api/v1/search?q=report")

        self.assertEqual(self._names(resp), ["late", "on time"])
        self.assertFalse(resp.json()["is_partial"])

    def test_a_complete_answer_is_cached(self):
        self.files_is_slow = False
        self.client.get("/api/v1/search?q=report")
        self.files_is_slow = True

        resp = self.client.get("/api/v1/search?q=report")

        self.assertEqual(self._names(resp), ["late", "on time"])
        self.assertNotIn("no-store", resp["Cache-Control"])
