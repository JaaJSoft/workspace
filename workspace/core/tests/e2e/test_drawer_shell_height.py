"""The drawer shell must never grow past the viewport, whatever a sidebar holds.

`.drawer` is a CSS grid, and daisyUI leaves its single row auto-sized because it
assumes the drawer *is* the viewport. Our shell puts it under a navbar instead,
and every module's sidebar asks for `height: 100%` — a percentage against an
auto track, which is circular, so the browser sizes the track on the sidebar's
content. One long conversation list then stretches the content column with it:
the pane's header leaves the screen and `body { overflow: hidden }` clips it
rather than offering a scrollbar. `grid-rows-1` on `.drawer` bounds the track.

Chat is the vehicle, not the subject — the shell is shared by seven modules.
The assertions are on computed geometry, since a class-list assertion would
pass against the broken layout.
"""

from workspace.chat.models import Conversation, ConversationMember
from workspace.common.tests.e2e.base import PlaywrightTestCase

# Comfortably more than a 720px-tall viewport holds at ~58px per row.
CONVERSATION_COUNT = 30

GEOMETRY = """() => {
  const box = el => Math.round(el.getBoundingClientRect().height);
  const listWrapper = document.querySelector('#conversation-list').parentElement;
  const aside = document.querySelector('.drawer-side aside');
  return {
    viewport: window.innerHeight,
    drawer: box(document.querySelector('.drawer')),
    drawerSide: box(document.querySelector('.drawer-side')),
    main: box(document.querySelector('main')),
    asideBottom: Math.round(aside.getBoundingClientRect().bottom),
    listScrolls: listWrapper.scrollHeight > listWrapper.clientHeight + 1,
  };
}"""


class DrawerShellHeightTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        user = self.create_user(username="alice")
        for i in range(CONVERSATION_COUNT):
            conversation = Conversation.objects.create(
                kind=Conversation.Kind.GROUP,
                title=f"Team channel {i:02d}",
                created_by=user,
            )
            ConversationMember.objects.create(conversation=conversation, user=user)
        self.login_as(user)

    def test_a_sidebar_taller_than_the_viewport_scrolls_instead_of_growing(self):
        self.page.goto(f"{self.live_server_url}/chat")
        self.page.wait_for_selector("#conversation-list li")
        self.page.wait_for_timeout(500)

        geometry = self.page.evaluate(GEOMETRY)

        # The drawer itself is correct even when broken (it is a flex item);
        # what regresses is everything the grid row drags along with it.
        self.assertLessEqual(geometry["drawerSide"], geometry["drawer"] + 1, geometry)
        self.assertLessEqual(geometry["main"], geometry["drawer"] + 1, geometry)
        self.assertLessEqual(
            geometry["asideBottom"], geometry["viewport"] + 1, geometry
        )
        # The list has its own `overflow-y-auto`; it only ever fires once the
        # sidebar stops being free to grow to fit its contents.
        self.assertTrue(geometry["listScrolls"], geometry)
