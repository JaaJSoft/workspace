"""E2E: the notifications store pages through the list with limit and offset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.notifications.models import Notification

START = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)


class NotificationsStorePaginationTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user(username="pager")
        for i in range(25):
            self._notify(f"Notif {i}", START + timedelta(minutes=i))
        self.login_as(self.user)
        self.page.goto(f"{self.live_server_url}/chat")
        self.page.wait_for_load_state("networkidle")

    def _notify(self, title, created_at):
        notif = Notification.objects.create(
            recipient=self.user, origin="chat", icon="bell", title=title
        )
        # auto_now_add ignores a value passed to create().
        Notification.objects.filter(pk=notif.pk).update(created_at=created_at)

    def _store(self, expression):
        return self.page.evaluate(
            f"async () => {{ const s = Alpine.store('notifications'); {expression} }}"
        )

    def _state(self):
        return self._store(
            "return { titles: s.items.map(n => n.title), hasMore: s.hasMore };"
        )

    def test_load_more_appends_the_next_page(self):
        self._store("await s.fetchList();")
        first = self._state()
        self.assertEqual(len(first["titles"]), 20)
        self.assertEqual(first["titles"][0], "Notif 24")
        self.assertTrue(first["hasMore"])

        self._store("await s.loadMore();")
        state = self._state()
        self.assertEqual(state["titles"], [f"Notif {i}" for i in range(24, -1, -1)])
        self.assertFalse(state["hasMore"])

    def test_notification_arriving_between_pages_is_not_listed_twice(self):
        self._store("await s.fetchList();")
        self._notify("Newcomer", START + timedelta(hours=1))

        self._store("await s.loadMore();")
        self._store("await s.loadMore();")

        state = self._state()
        self.assertEqual(state["titles"], [f"Notif {i}" for i in range(24, -1, -1)])
        self.assertFalse(state["hasMore"])
