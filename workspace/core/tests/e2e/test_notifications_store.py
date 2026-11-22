"""E2E: the notifications store pages through the list with limit and offset."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.notifications.models import Notification

START = datetime(2024, 1, 1, 10, 0, tzinfo=UTC)
ALL_TITLES = [f"Notif {i}" for i in range(24, -1, -1)]


class NotificationsStoreTestCase(PlaywrightTestCase):
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


class NotificationsStorePaginationTests(NotificationsStoreTestCase):
    def test_load_more_appends_the_next_page(self):
        self._store("await s.fetchList();")
        first = self._state()
        self.assertEqual(len(first["titles"]), 20)
        self.assertEqual(first["titles"][0], "Notif 24")
        self.assertTrue(first["hasMore"])

        self._store("await s.loadMore();")
        state = self._state()
        self.assertEqual(state["titles"], ALL_TITLES)
        self.assertFalse(state["hasMore"])

    def test_notification_arriving_between_pages_is_not_listed_twice(self):
        self._store("await s.fetchList();")
        self._notify("Newcomer", START + timedelta(hours=1))

        self._store("await s.loadMore();")
        self._store("await s.loadMore();")

        state = self._state()
        self.assertEqual(state["titles"], ALL_TITLES)
        self.assertFalse(state["hasMore"])


class NotificationsStoreLastRowChangesTests(NotificationsStoreTestCase):
    """Regression: the next page was cut after the last loaded row, looked up
    again on the server. Once that row moved or left the list, the cursor
    fell back to the top and Load more repeated the first page."""

    LAST_LOADED = "Notif 5"

    def _assert_no_repeat(self):
        titles = self._state()["titles"]
        self.assertEqual(len(titles), len(set(titles)), titles)
        self.assertIn("Notif 0", titles)

    def test_merged_last_row(self):
        self._store("await s.fetchList();")
        # What notify_stream does to the row of a conversation with a new message.
        Notification.objects.filter(title=self.LAST_LOADED).update(
            created_at=START + timedelta(hours=1)
        )
        self._store("await s.loadMore();")
        self.assertEqual(self._state()["titles"], ALL_TITLES)

    def test_last_row_read_under_the_unread_filter(self):
        self._store("await s.fetchList({ filter: 'unread' });")
        Notification.objects.filter(title=self.LAST_LOADED).update(read_at=START)
        self._store("await s.loadMore();")
        self._assert_no_repeat()

    def test_last_row_deleted(self):
        self._store("await s.fetchList();")
        Notification.objects.filter(title=self.LAST_LOADED).delete()
        self._store("await s.loadMore();")
        self._assert_no_repeat()


class NotificationsStoreReconnectTests(NotificationsStoreTestCase):
    """Regression: a stream reconnection reloaded only the first page, cutting
    the list back to 20 entries after Load more."""

    def _reconnect(self):
        # The listener starts the fetch synchronously, so `loading` is already set.
        self._store(
            "window.dispatchEvent(new CustomEvent('sse:reconnect'));"
            "while (s.loading) await new Promise(r => setTimeout(r, 10));"
        )

    def test_reconnect_keeps_the_pages_loaded_with_load_more(self):
        self._store("await s.fetchList(); await s.loadMore();")
        self._notify("Newcomer", START + timedelta(hours=1))

        self._reconnect()

        state = self._state()
        self.assertEqual(state["titles"], ["Newcomer", *ALL_TITLES[:24]])
        self.assertTrue(state["hasMore"])

    def test_reconnect_still_reloads_a_single_page(self):
        self._store("await s.fetchList();")
        self._notify("Newcomer", START + timedelta(hours=1))

        self._reconnect()

        state = self._state()
        self.assertEqual(state["titles"], ["Newcomer", *ALL_TITLES[:19]])
        self.assertTrue(state["hasMore"])
