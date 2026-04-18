from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from workspace.core import changelog as changelog_module


class ParseChangelogTests(TestCase):
    def setUp(self):
        cache.clear()

    def _parse(self, md):
        with patch.object(changelog_module, '_CHANGELOG_PATH') as path_mock:
            path_mock.read_text.return_value = md
            return changelog_module._parse_changelog()

    def test_version_only_has_empty_title(self):
        entries = self._parse(
            "## 0.17.0\n\n### Highlights\n\nStuff.\n"
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['version'], '0.17.0')
        self.assertEqual(entries[0]['title'], '')

    def test_version_with_em_dash_title(self):
        entries = self._parse(
            "## 0.18.0 \u2014 Performance & Reliability\n\n"
            "### Highlights\n\nFast stuff.\n"
        )
        self.assertEqual(entries[0]['version'], '0.18.0')
        self.assertEqual(entries[0]['title'], 'Performance & Reliability')

    def test_version_with_en_dash_title(self):
        entries = self._parse(
            "## 0.18.0 \u2013 Calendar\n\n### Highlights\n\nX.\n"
        )
        self.assertEqual(entries[0]['title'], 'Calendar')

    def test_version_with_hyphen_title(self):
        entries = self._parse(
            "## 0.18.0 - Perf\n\n### Highlights\n\nX.\n"
        )
        self.assertEqual(entries[0]['title'], 'Perf')

    def test_version_with_colon_title(self):
        entries = self._parse(
            "## 0.18.0: Perf\n\n### Highlights\n\nX.\n"
        )
        self.assertEqual(entries[0]['title'], 'Perf')

    def test_multiple_entries_keep_order(self):
        entries = self._parse(
            "## 0.18.0 \u2014 Newer\n\n### Highlights\n\nA.\n\n"
            "## 0.17.0\n\n### Highlights\n\nB.\n"
        )
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]['version'], '0.18.0')
        self.assertEqual(entries[0]['title'], 'Newer')
        self.assertEqual(entries[1]['version'], '0.17.0')
        self.assertEqual(entries[1]['title'], '')

    def test_html_body_is_rendered(self):
        entries = self._parse(
            "## 0.1.0\n\n### Highlights\n\nFast stuff.\n"
        )
        self.assertIn('Fast stuff', entries[0]['html'])
        self.assertIn('<h3>', entries[0]['html'])

    def test_file_not_found_returns_empty_list(self):
        with patch.object(changelog_module, '_CHANGELOG_PATH') as path_mock:
            path_mock.read_text.side_effect = FileNotFoundError
            entries = changelog_module._parse_changelog()
        self.assertEqual(entries, [])
