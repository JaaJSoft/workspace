from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase

from workspace.core.context_processors import apple_keyboard

User = get_user_model()

MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"
)
WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
)
LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:145.0) Gecko/20100101 Firefox/145.0"


class AppleKeyboardTests(SimpleTestCase):
    def _detect(self, agent=None):
        headers = {"User-Agent": agent} if agent is not None else {}
        return apple_keyboard(RequestFactory().get("/", headers=headers))

    def test_apple_devices_press_command(self):
        self.assertTrue(self._detect(MAC))
        self.assertTrue(self._detect(IPHONE))

    def test_other_systems_press_ctrl(self):
        self.assertFalse(self._detect(WINDOWS))
        self.assertFalse(self._detect(LINUX))

    def test_no_user_agent_falls_back_to_ctrl(self):
        self.assertFalse(self._detect())


class SearchShortcutHintTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")
        self.client.force_login(self.user)

    def tearDown(self):
        cache.clear()

    def _search_kbd(self, agent):
        html = self.client.get("/files", headers={"User-Agent": agent}).content.decode()
        start = html.index('<kbd\n        class="kbd')
        return html[start : html.index("</kbd>", start)]

    def test_windows_shows_ctrl(self):
        kbd = self._search_kbd(WINDOWS)
        self.assertIn("Ctrl K", kbd)
        self.assertNotIn("⌘", kbd)

    def test_mac_shows_command(self):
        kbd = self._search_kbd(MAC)
        self.assertIn('data-lucide="command"', kbd)
        self.assertNotIn("Ctrl", kbd)
