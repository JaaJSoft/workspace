"""Discovery parsing, caching and editor-URL building."""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from workspace.files.services.wopi import discovery

DISCOVERY_XML = """<?xml version="1.0" encoding="utf-8"?>
<wopi-discovery>
  <net-zone name="external-http">
    <app name="writer" favIconUrl="https://editor/writer.ico">
      <action name="edit" ext="docx" urlsrc="https://editor/browser/abc/cool.html?"/>
      <action name="edit" ext="odt" urlsrc="https://editor/browser/abc/cool.html?"/>
      <action name="view" ext="doc" urlsrc="https://editor/browser/abc/cool.html?"/>
    </app>
    <app name="calc">
      <action name="edit" ext="xlsx" urlsrc="https://editor/browser/abc/cool.html?&lt;ui=UI_LLCC&amp;&gt;&lt;rs=DC_LLCC&amp;&gt;"/>
    </app>
  </net-zone>
</wopi-discovery>
"""


class _FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, text):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        return _FakeResponse(self._text)


@override_settings(WOPI_DISCOVERY_URL="https://editor/hosting/discovery")
class DiscoveryTests(TestCase):
    def setUp(self):
        cache.clear()
        discovery._reset_process_memo()

    def tearDown(self):
        cache.clear()
        discovery._reset_process_memo()

    def _patch_fetch(self, text=DISCOVERY_XML):
        return patch(
            "workspace.files.services.wopi.discovery.httpx2.Client",
            lambda **kwargs: _FakeClient(text),
        )

    def test_actions_are_parsed_per_extension(self):
        with self._patch_fetch():
            actions = discovery.get_actions()
        self.assertIn("docx", actions)
        self.assertIn("edit", actions["docx"])
        self.assertIn("xlsx", actions)

    def test_placeholders_are_stripped_from_urlsrc(self):
        with self._patch_fetch():
            url = discovery.get_action_url("xlsx", "edit")
        self.assertEqual(url, "https://editor/browser/abc/cool.html?")

    def test_missing_view_action_falls_back_to_edit(self):
        with self._patch_fetch():
            url = discovery.get_action_url("docx", "view")
        self.assertEqual(url, "https://editor/browser/abc/cool.html?")

    def test_unknown_extension_yields_none(self):
        with self._patch_fetch():
            self.assertIsNone(discovery.get_action_url("zip", "view"))

    def test_result_is_cached_across_calls(self):
        with self._patch_fetch():
            discovery.get_actions()
        # No patch active: a second fetch would hit the real network and fail.
        self.assertIn("docx", discovery.get_actions())

    def test_fetch_failure_is_cached_and_returns_none(self):
        with self._patch_fetch(text="not xml <<<"):
            self.assertIsNone(discovery.get_actions())
        # The failure marker short-circuits the retry.
        self.assertIsNone(discovery.get_actions())

    @override_settings(WOPI_DISCOVERY_URL="")
    def test_disabled_editor_yields_none_without_fetching(self):
        self.assertIsNone(discovery.get_actions())

    def test_supported_extensions_lists_advertised_formats(self):
        with self._patch_fetch():
            extensions = discovery.supported_extensions()
        self.assertEqual(extensions, frozenset({"docx", "odt", "doc", "xlsx"}))

    @override_settings(WOPI_DISCOVERY_URL="")
    def test_supported_extensions_is_none_when_disabled(self):
        self.assertIsNone(discovery.supported_extensions())


class BuildEditorUrlTests(TestCase):
    def test_trailing_question_mark_appends_directly(self):
        url = discovery.build_editor_url(
            "https://e/cool.html?", "https://w/api/wopi/files/x"
        )
        self.assertEqual(
            url, "https://e/cool.html?WOPISrc=https%3A%2F%2Fw%2Fapi%2Fwopi%2Ffiles%2Fx"
        )

    def test_existing_query_appends_with_ampersand(self):
        url = discovery.build_editor_url("https://e/cool.html?a=1", "https://w/f")
        self.assertTrue(url.startswith("https://e/cool.html?a=1&WOPISrc="))

    def test_bare_url_appends_with_question_mark(self):
        url = discovery.build_editor_url("https://e/cool.html", "https://w/f")
        self.assertTrue(url.startswith("https://e/cool.html?WOPISrc="))
