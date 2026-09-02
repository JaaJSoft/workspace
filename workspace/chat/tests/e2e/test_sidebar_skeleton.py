"""The chat sidebar must never paint empty while Alpine boots.

The conversation list is server-rendered but cloaked until Alpine binds its
rows, and Alpine is deferred, so the sidebar used to show a blank column for
the first frames of every page load and the rows popped in afterwards. A
skeleton now overlays that space until Alpine mounts, sized from the same
server context as the list itself (row count, pinned section, collapsed
preference, compact density), so the swap is a fade rather than a reflow.

Two observations pin that down, both installed before any document script
runs. A MutationObserver reads the computed styles the instant the parser
inserts the list - long before deferred scripts, so Alpine cannot have run:
the skeleton is displayed, the list is not, and every skeleton row's box is
recorded. A requestAnimationFrame sampler then records each distinct
(skeleton, list) visibility pair the page paints, which must never be
(hidden, hidden). After Alpine mounts, the real rows must sit exactly where
the skeleton rows were.
"""

from __future__ import annotations

from django.core.cache import cache

from workspace.chat.models import Conversation, ConversationMember, PinnedConversation
from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.users.services.settings import set_setting

DESKTOP = {"width": 1280, "height": 900}
MOBILE = {"width": 375, "height": 667}

OBSERVER = """
window.__sidebar = { atParse: null, samples: [] };
const rect = (el) => {
  const r = el.getBoundingClientRect();
  return { top: Math.round(r.top), left: Math.round(r.left),
           width: Math.round(r.width), height: Math.round(r.height) };
};
const visible = (el) => {
  const cs = getComputedStyle(el);
  return cs.display !== 'none' && cs.visibility !== 'hidden';
};
const skeleton = () => document.querySelector('[data-testid="conversation-list-skeleton"]');
const list = () => document.getElementById('conversation-list');
// The list is parsed after the skeleton, so once it exists the skeleton is
// complete. The script runs before <html> exists, so the document is the
// only node there is to observe.
const observer = new MutationObserver(() => {
  const s = skeleton(), l = list();
  if (!s || !l || window.__sidebar.atParse) return;
  window.__sidebar.atParse = {
    skeleton: visible(s),
    list: visible(l),
    rows: [...s.querySelectorAll('li')].map(rect),
  };
  observer.disconnect();
});
observer.observe(document, { childList: true, subtree: true });
(function sample() {
  const s = skeleton(), l = list();
  if (s && l) {
    const pair = [visible(s), visible(l)];
    const samples = window.__sidebar.samples;
    const last = samples[samples.length - 1];
    if (!last || last[0] !== pair[0] || last[1] !== pair[1]) samples.push(pair);
  }
  if (performance.now() < 5000) requestAnimationFrame(sample);
})();
"""

REAL_ROWS = """() => [...document.querySelectorAll('#conversation-list li')].map(li => {
  const r = li.getBoundingClientRect();
  return { top: Math.round(r.top), left: Math.round(r.left),
           width: Math.round(r.width), height: Math.round(r.height) };
})"""


class ChatSidebarSkeletonTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="viewer")
        peers = [self.create_user(username=f"peer-{i}") for i in range(4)]
        self.conversations = []
        for peer in peers:
            conv = Conversation.objects.create(
                kind=Conversation.Kind.DM, created_by=self.user
            )
            ConversationMember.objects.create(conversation=conv, user=self.user)
            ConversationMember.objects.create(conversation=conv, user=peer)
            self.conversations.append(conv)
        # A pinned section on top of the merged list: the skeleton has to
        # reproduce its header and divider, not only the rows.
        PinnedConversation.objects.create(
            owner=self.user, conversation=self.conversations[0], position=0
        )
        self.login_as(self.user)
        self.page.set_viewport_size(DESKTOP)
        self.context.add_init_script(OBSERVER)

    def tearDown(self):
        cache.clear()
        super().tearDown()

    def _load(self):
        self.page.goto(f"{self.live_server_url}/chat")
        self.page.wait_for_selector("#conversation-list li")
        # Long enough for Alpine to bind and the avatar elements to upgrade.
        self.page.wait_for_timeout(1200)
        return {
            "at_parse": self.page.evaluate("window.__sidebar.atParse"),
            "samples": self.page.evaluate("window.__sidebar.samples"),
            "final_rows": self.page.evaluate(REAL_ROWS),
            "skeleton_hidden": self.page.locator(
                '[data-testid="conversation-list-skeleton"]'
            ).is_hidden(),
            "list_visible": self.page.locator("#conversation-list").is_visible(),
        }

    def _assert_seamless(self, seen):
        at_parse = seen["at_parse"]
        self.assertIsNotNone(at_parse, seen)
        self.assertTrue(at_parse["skeleton"], seen)
        self.assertFalse(at_parse["list"], seen)
        self.assertTrue(seen["skeleton_hidden"], seen)
        self.assertTrue(seen["list_visible"], seen)
        self.assertNotIn([False, False], seen["samples"], seen)
        self.assertEqual(seen["final_rows"], at_parse["rows"], seen)
        self.assertEqual(len(seen["final_rows"]), len(self.conversations), seen)

    def test_expanded_sidebar_paints_the_skeleton_where_the_rows_land(self):
        self._assert_seamless(self._load())

    def test_compact_rows_get_a_compact_skeleton(self):
        set_setting(self.user, "chat", "preferences", {"compactConversationList": True})
        self._assert_seamless(self._load())

    def test_collapsed_sidebar_gets_an_icon_rail_skeleton(self):
        set_setting(self.user, "chat", "sidebar_collapsed", True)
        self._assert_seamless(self._load())

    def test_mobile_rail_gets_an_icon_rail_skeleton_whatever_the_preference(self):
        self.page.set_viewport_size(MOBILE)
        self._assert_seamless(self._load())
