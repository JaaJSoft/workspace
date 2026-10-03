"""The global search's "Mine only" switch, from the API to the providers.

Each scoped provider pins what it drops in its own module's tests; these pin
the plumbing: which providers receive the scope, how the API reads it, and how
the saved preference reaches the palette.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from workspace.core.context_processors import workspace_modules
from workspace.core.module_registry import (
    ModuleInfo,
    ModuleRegistry,
    SearchProviderInfo,
    SearchResult,
    SearchScope,
)
from workspace.core.setting_keys import SEARCH_SCOPE
from workspace.users.services.settings import set_setting

User = get_user_model()


def _hit(name):
    return SearchResult(
        uuid=name,
        name=name,
        url="/",
        matched_value=name,
        match_type="title",
        type_icon="i",
        module_slug="files",
    )


class RegistryScopeTests(TestCase):
    def setUp(self):
        self.reg = ModuleRegistry()
        self.reg.register(
            ModuleInfo(
                name="Files",
                slug="files",
                description="",
                icon="i",
                color="indigo",
                url="/files",
            )
        )
        self.scopes = []

        def scoped(q, u, limit, scope):
            self.scopes.append(scope)
            return [_hit("scoped")]

        self.reg.register_search_provider(
            SearchProviderInfo(
                slug="scoped", module_slug="files", search_fn=scoped, scoped=True
            )
        )
        # Positional-only, like every provider written before scopes existed:
        # passing it a scope would raise and drop its hits.
        self.reg.register_search_provider(
            SearchProviderInfo(
                slug="unscoped",
                module_slug="files",
                search_fn=lambda q, u, limit: [_hit("unscoped")],
            )
        )

    def test_a_scoped_provider_receives_the_scope(self):
        self.reg.search("doc", user=None, scope=SearchScope.MINE)
        self.assertEqual(self.scopes, [SearchScope.MINE])

    def test_the_scope_defaults_to_all(self):
        self.reg.search("doc", user=None)
        self.assertEqual(self.scopes, [SearchScope.ALL])

    def test_an_unscoped_provider_answers_the_same_under_mine(self):
        hits = self.reg.search("doc", user=None, scope=SearchScope.MINE)
        self.assertEqual({h["name"] for h in hits}, {"scoped", "unscoped"})


class UnifiedSearchScopeParamTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _scope_for(self, query_string):
        with patch(
            "workspace.core.services.search.search_modules", return_value=[]
        ) as search_modules:
            resp = self.client.get(f"/api/v1/search?q=doc{query_string}")
        self.assertEqual(resp.status_code, 200)
        return search_modules.call_args.kwargs["scope"]

    def test_mine_reaches_the_providers(self):
        self.assertEqual(self._scope_for("&scope=mine"), SearchScope.MINE)

    def test_missing_scope_searches_everything(self):
        self.assertEqual(self._scope_for(""), SearchScope.ALL)

    def test_unknown_scope_searches_everything(self):
        self.assertEqual(self._scope_for("&scope=shared"), SearchScope.ALL)

    def test_each_scope_is_cached_apart(self):
        # The response cache keys on the query string; a scope read from the
        # setting instead would serve the other scope's hits after a toggle.
        self.assertEqual(self._scope_for("&scope=mine"), SearchScope.MINE)
        self.assertEqual(self._scope_for("&scope=all"), SearchScope.ALL)


class SearchScopeContextTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="alice", password="x")

    def tearDown(self):
        cache.clear()

    def _scope(self, user=None):
        request = self.factory.get("/")
        request.user = user or self.user
        return workspace_modules(request)["workspace_search_scope"]

    def test_defaults_to_all(self):
        self.assertEqual(self._scope(), SearchScope.ALL)

    def test_reads_the_saved_scope(self):
        set_setting(self.user, "core", SEARCH_SCOPE, SearchScope.MINE)
        self.assertEqual(self._scope(), SearchScope.MINE)

    def test_anonymous_users_search_everything(self):
        self.assertEqual(self._scope(AnonymousUser()), SearchScope.ALL)

    def test_embedded_in_the_page_for_the_palette(self):
        set_setting(self.user, "core", SEARCH_SCOPE, SearchScope.MINE)
        self.client.force_login(self.user)
        html = self.client.get("/files").content.decode()
        self.assertIn(
            '<script id="search-scope-data" type="application/json">"mine"</script>',
            html,
        )
