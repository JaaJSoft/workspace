"""E2E: a page asks for the presence of the users it displays.

The stream only carries the users a page watched, so an avatar of someone
the viewer never displayed before has to fetch its status itself. That
round trip (a status read in the store, a batched request, the answer
patched into the ring) runs across the custom element, the Alpine store
and the API, which only a browser puts together.
"""

from __future__ import annotations

from django.utils import timezone

from workspace.common.tests.e2e.base import PlaywrightTestCase
from workspace.users.models import UserPresence
from workspace.users.services import presence_stream


class PresenceWatchTests(PlaywrightTestCase):
    def setUp(self):
        super().setUp()
        self.viewer = self.create_user(username="viewer")
        self.other = self.create_user(username="someone-online")
        now = timezone.now()
        UserPresence.objects.create(
            user=self.other, last_seen=now, last_activity=now, manual_status="busy"
        )
        presence_stream._instance_cache["value"] = None
        self.login_as(self.viewer)
        self.page.goto(f"{self.live_server_url}/files")
        self.page.wait_for_load_state("networkidle")

    def test_an_avatar_of_an_unwatched_user_gets_its_status(self):
        with self.page.expect_request("**/api/v1/users/presence") as request:
            self.page.evaluate(
                """
                (uid) => {
                  const host = document.createElement('div');
                  host.innerHTML =
                    `<user-avatar id="probe" user-id="${uid}" username="Probe" size="md" presence></user-avatar>`;
                  document.body.appendChild(host);
                }
                """,
                self.other.id,
            )
        self.assertIn(self.other.id, request.value.post_data_json["user_ids"])

        self.page.wait_for_selector("#probe > .ring-error")
