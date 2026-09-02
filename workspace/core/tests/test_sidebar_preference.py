"""The shell renders the current module's collapsed-sidebar preference.

Alpine binds only after the document is parsed, so a sidebar whose width lived
in a binding painted at the wrong width first. The context processor exposes
the preference so the module templates can put the final width class in the
HTML itself.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import RequestFactory, TestCase

from workspace.core.context_processors import workspace_modules
from workspace.core.setting_keys import SIDEBAR_COLLAPSED
from workspace.users.services.settings import set_setting

User = get_user_model()


class SidebarCollapsedContextTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = User.objects.create_user(username="alice", password="x")

    def tearDown(self):
        cache.clear()

    def _collapsed(self, path, user=None):
        request = self.factory.get(path)
        request.user = user or self.user
        return workspace_modules(request)["workspace_sidebar_collapsed"]

    def test_defaults_to_expanded(self):
        self.assertIs(self._collapsed("/files"), False)

    def test_reads_the_current_module_setting_anywhere_in_the_module(self):
        set_setting(self.user, "files", SIDEBAR_COLLAPSED, True)
        self.assertIs(self._collapsed("/files"), True)
        self.assertIs(self._collapsed("/files/some/folder"), True)

    def test_each_module_has_its_own_preference(self):
        set_setting(self.user, "files", SIDEBAR_COLLAPSED, True)
        self.assertIs(self._collapsed("/chat"), False)

    def test_only_a_true_value_collapses(self):
        # The settings API stores any JSON; a stray string must not become a
        # class the template never asked for.
        set_setting(self.user, "files", SIDEBAR_COLLAPSED, "true")
        self.assertIs(self._collapsed("/files"), False)

    def test_expanded_outside_a_module_and_for_anonymous_users(self):
        set_setting(self.user, "files", SIDEBAR_COLLAPSED, True)
        self.assertIs(self._collapsed("/"), False)
        self.assertIs(self._collapsed("/files", AnonymousUser()), False)

    def test_embedded_in_the_page_for_the_component(self):
        set_setting(self.user, "files", SIDEBAR_COLLAPSED, True)
        self.client.force_login(self.user)
        html = self.client.get("/files").content.decode()
        self.assertIn(
            '<script id="sidebar-collapsed-data" type="application/json">true</script>',
            html,
        )
