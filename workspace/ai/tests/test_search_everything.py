"""Tests for the search_everything AI tool."""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.ai.tools import (
    SEARCH_EVERYTHING_MAX_LIMIT,
    SearchEverythingParams,
    SearchToolProvider,
)
from workspace.files.models import File

User = get_user_model()


class SearchEverythingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="alice", password="pass")
        cls.other = User.objects.create_user(username="bob", password="pass")

    def _call(self, query, limit=None):
        kwargs = {"query": query}
        if limit is not None:
            kwargs["limit"] = limit
        return SearchToolProvider().search_everything(
            SearchEverythingParams(**kwargs),
            user=self.user,
            bot=None,
            conversation_id=None,
            context={},
        )

    def test_finds_a_file_and_reports_where_it_lives(self):
        note = File.objects.create(
            name="Alpha migration plan",
            owner=self.user,
            node_type=File.NodeType.FILE,
        )

        payload = json.loads(self._call("Alpha migration"))

        hit = next(h for h in payload if h["uuid"] == str(note.uuid))
        self.assertEqual(hit["name"], "Alpha migration plan")
        self.assertEqual(hit["module"], "files")
        self.assertEqual(hit["type"], "files")
        self.assertIn(str(note.uuid), hit["url"])

    def test_does_not_leak_other_users_content(self):
        File.objects.create(
            name="Alpha migration plan",
            owner=self.other,
            node_type=File.NodeType.FILE,
        )

        self.assertEqual(
            self._call("Alpha migration"), 'Nothing found for "Alpha migration".'
        )

    def test_short_query_is_rejected(self):
        self.assertEqual(self._call("a"), "Error: query must be at least 2 characters")

    @patch("workspace.core.services.search.search_modules", return_value=[])
    def test_limit_is_capped_below_the_ui_default(self, mock_search):
        self._call("alpha", limit=50)

        self.assertEqual(mock_search.call_args.args[2], SEARCH_EVERYTHING_MAX_LIMIT)

    @patch("workspace.core.services.search.search_modules", return_value=[])
    def test_non_positive_limit_still_asks_for_one_hit(self, mock_search):
        self._call("alpha", limit=0)

        self.assertEqual(mock_search.call_args.args[2], 1)


class SearchEverythingPayloadTests(TestCase):
    """Field mapping from a registry hit to the tool's JSON output."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="carol", password="pass")

    def _hit(self, **overrides):
        hit = {
            "uuid": "u-1",
            "name": "Weekly sync",
            "url": "/calendar?event=u-1",
            "matched_value": "Weekly sync",
            "match_type": "title",
            "type_icon": "calendar",
            "module_slug": "calendar",
            "module_color": "accent",
            "date": None,
            "tags": (),
            "provider_slug": "calendar",
        }
        hit.update(overrides)
        return hit

    def _call(self, hits):
        with patch("workspace.core.services.search.search_modules", return_value=hits):
            return json.loads(
                SearchToolProvider().search_everything(
                    SearchEverythingParams(query="sync"),
                    user=self.user,
                    bot=None,
                    conversation_id=None,
                    context={},
                )
            )

    def test_matched_value_is_omitted_when_it_repeats_the_name(self):
        entry = self._call([self._hit()])[0]
        self.assertNotIn("matched_on", entry)
        self.assertNotIn("date", entry)

    def test_matched_value_is_kept_when_the_hit_came_from_another_field(self):
        entry = self._call(
            [
                self._hit(
                    matched_value="Room 3, Paris",
                    match_type="location",
                    date="2026-02-01",
                )
            ]
        )[0]
        self.assertEqual(entry["matched_on"], "location: Room 3, Paris")
        self.assertEqual(entry["date"], "2026-02-01")
