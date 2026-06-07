"""End-to-end tests verifying that REST endpoints emit FileEvent rows.

Each test exercises a real viewset action and asserts the corresponding
event was recorded with the expected actor, action, and metadata. This
is the safety net guarding against silent regressions when wiring of
``record_event`` is moved or removed during refactors.
"""

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from rest_framework import status
from rest_framework.test import APITestCase

from workspace.files.models import File, FileEvent, FileShare, FileShareLink

User = get_user_model()


class FileEventCreateTests(APITestCase):
    """POST /api/v1/files writes a CREATED event."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pass",
        )
        self.client.force_authenticate(user=self.user)

    def test_create_folder_emits_created_event(self):
        response = self.client.post(
            "/api/v1/files",
            {
                "name": "Docs",
                "node_type": "folder",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        folder = File.objects.get(name="Docs")
        events = list(FileEvent.objects.filter(file=folder))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, FileEvent.Action.CREATED)
        self.assertEqual(events[0].actor, self.user)

    def test_create_file_with_content_emits_created_event(self):
        response = self.client.post(
            "/api/v1/files",
            {
                "name": "note.txt",
                "node_type": "file",
                "content": ContentFile(b"hello", name="note.txt"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        file_obj = File.objects.get(name="note.txt")
        events = list(FileEvent.objects.filter(file=file_obj))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, FileEvent.Action.CREATED)


class FileEventUpdateTests(APITestCase):
    """PATCH /api/v1/files/<uuid> writes RENAMED / MOVED / CONTENT_REPLACED."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pass",
        )
        self.client.force_authenticate(user=self.user)
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        # Setup created an initial event via the model? No — direct ORM
        # create bypasses the viewset, so the event log starts empty here.
        FileEvent.objects.all().delete()

    def test_rename_emits_renamed_event_with_old_and_new(self):
        response = self.client.patch(
            f"/api/v1/files/{self.file.uuid}",
            {
                "name": "new-doc.txt",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.action, FileEvent.Action.RENAMED)
        self.assertEqual(ev.actor, self.user)
        self.assertEqual(ev.metadata["old_name"], "doc.txt")
        self.assertEqual(ev.metadata["new_name"], "new-doc.txt")

    def test_move_emits_moved_event_with_parent_uuids(self):
        target_folder = File.objects.create(
            owner=self.user,
            name="Archive",
            node_type=File.NodeType.FOLDER,
        )
        FileEvent.objects.all().delete()

        response = self.client.patch(
            f"/api/v1/files/{self.file.uuid}",
            {
                "parent": str(target_folder.uuid),
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.action, FileEvent.Action.MOVED)
        self.assertIsNone(ev.metadata["old_parent_id"])
        self.assertEqual(ev.metadata["new_parent_id"], str(target_folder.uuid))

    def test_content_replace_emits_content_replaced_event(self):
        new_content = ContentFile(b"updated", name="doc.txt")

        response = self.client.patch(
            f"/api/v1/files/{self.file.uuid}",
            {
                "content": new_content,
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, FileEvent.Action.CONTENT_REPLACED)

    def test_no_op_update_emits_no_event(self):
        response = self.client.patch(
            f"/api/v1/files/{self.file.uuid}",
            {
                "name": "doc.txt",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(FileEvent.objects.filter(file=self.file).count(), 0)


class FileEventDeleteRestoreTests(APITestCase):
    """DELETE / restore emit DELETED / RESTORED events."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pass",
        )
        self.client.force_authenticate(user=self.user)
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        FileEvent.objects.all().delete()

    def test_destroy_emits_deleted_event(self):
        response = self.client.delete(f"/api/v1/files/{self.file.uuid}")

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        # The file is soft-deleted, so the row still exists alongside the event.
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, FileEvent.Action.DELETED)
        self.assertEqual(events[0].actor, self.user)

    def test_restore_emits_restored_event(self):
        self.file.soft_delete()

        response = self.client.post(f"/api/v1/files/{self.file.uuid}/restore")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, FileEvent.Action.RESTORED)
        self.assertEqual(events[0].actor, self.user)


class FileEventShareTests(APITestCase):
    """POST/DELETE /share emit SHARED / UNSHARED / SHARE_PERMISSION_CHANGED."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            email="o@test.com",
            password="pass",
        )
        self.recipient = User.objects.create_user(
            username="friend",
            email="f@test.com",
            password="pass",
        )
        self.client.force_authenticate(user=self.owner)
        self.file = File.objects.create(
            owner=self.owner,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        self.file.content = ContentFile(b"Hello", name="doc.txt")
        self.file.size = 5
        self.file.save()
        FileEvent.objects.all().delete()

    def test_share_post_emits_shared_event(self):
        response = self.client.post(
            f"/api/v1/files/{self.file.uuid}/share",
            {
                "shared_with": self.recipient.pk,
                "permission": "ro",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.action, FileEvent.Action.SHARED)
        self.assertEqual(ev.metadata["shared_with_id"], self.recipient.pk)
        self.assertEqual(ev.metadata["shared_with_username"], "friend")
        self.assertEqual(ev.metadata["permission"], "ro")

    def test_share_permission_change_emits_permission_changed_event(self):
        # First share read-only
        self.client.post(
            f"/api/v1/files/{self.file.uuid}/share",
            {
                "shared_with": self.recipient.pk,
                "permission": "ro",
            },
            format="json",
        )
        FileEvent.objects.all().delete()

        # Then upgrade to rw
        response = self.client.post(
            f"/api/v1/files/{self.file.uuid}/share",
            {
                "shared_with": self.recipient.pk,
                "permission": "rw",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.action, FileEvent.Action.SHARE_PERMISSION_CHANGED)
        self.assertEqual(ev.metadata["old_permission"], "ro")
        self.assertEqual(ev.metadata["new_permission"], "rw")

    def test_share_delete_emits_unshared_event(self):
        FileShare.objects.create(
            file=self.file,
            shared_by=self.owner,
            shared_with=self.recipient,
        )

        response = self.client.delete(
            f"/api/v1/files/{self.file.uuid}/share",
            {
                "shared_with": self.recipient.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.action, FileEvent.Action.UNSHARED)
        self.assertEqual(ev.metadata["shared_with_id"], self.recipient.pk)


class FileEventShareLinkTests(APITestCase):
    """Share link create / revoke emit LINK_CREATED / LINK_REVOKED."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pass",
        )
        self.client.force_authenticate(user=self.user)
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        self.file.content = ContentFile(b"x", name="doc.txt")
        self.file.size = 1
        self.file.save()
        FileEvent.objects.all().delete()

    def test_link_create_emits_link_created_event(self):
        response = self.client.post(
            f"/api/v1/files/{self.file.uuid}/share-links",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.action, FileEvent.Action.LINK_CREATED)
        self.assertFalse(ev.metadata["has_password"])

    def test_link_delete_emits_link_revoked_event(self):
        link = FileShareLink.objects.create(
            file=self.file,
            created_by=self.user,
        )
        FileEvent.objects.all().delete()

        response = self.client.delete(
            f"/api/v1/files/{self.file.uuid}/share-links/{link.uuid}",
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        events = list(FileEvent.objects.filter(file=self.file))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].action, FileEvent.Action.LINK_REVOKED)


class PropertiesPanelEventsTests(APITestCase):
    """Properties partial embeds a lazy-loading stub for the activity timeline.

    The events themselves are fetched by alpine-ajax against the activity
    endpoint after the panel mounts; tests for that endpoint live in
    ``EventsPanelEndpointTests`` below. These tests only assert that the
    handoff (stub + auto-fetch URL) is in the rendered properties partial.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pass",
        )
        # The /files/properties view is a plain Django @login_required view
        # (not DRF), so session auth via client.login is required.
        self.client.login(username="alice", password="pass")
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        FileEvent.objects.all().delete()

    def test_panel_includes_lazy_load_stub_for_activity(self):
        response = self.client.get(f"/files/properties/{self.file.uuid}")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="file-events-list"', body)
        # The stub auto-fetches the activity endpoint via alpine-ajax on mount.
        self.assertIn(f"/files/{self.file.uuid}/events", body)

    def test_panel_does_not_render_events_inline(self):
        from workspace.files.services.events import record_event

        record_event(
            self.file,
            self.user,
            FileEvent.Action.RENAMED,
            {
                "old_name": "doc.txt",
                "new_name": "final.txt",
            },
        )

        response = self.client.get(f"/files/properties/{self.file.uuid}")
        body = response.content.decode()

        # Events are lazy-loaded, so the rename payload (final.txt) must
        # not appear in the initial properties body — it'll come back via
        # the alpine-ajax fetch against /files/<uuid>/events.
        self.assertNotIn("final.txt", body)


class EventsPanelEndpointTests(APITestCase):
    """GET /files/<uuid>/events — alpine-ajax target for "Load more"."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pass",
        )
        self.other_user = User.objects.create_user(
            username="bob",
            email="b@test.com",
            password="pass",
        )
        self.client.login(username="alice", password="pass")
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        FileEvent.objects.all().delete()

    def test_endpoint_renders_more_events_with_higher_limit(self):
        from workspace.files.services.events import record_event

        for i in range(40):
            record_event(
                self.file,
                self.user,
                FileEvent.Action.RENAMED,
                {
                    "old_name": "doc.txt",
                    "new_name": f"rev-{i}.txt",
                },
            )

        response = self.client.get(f"/files/{self.file.uuid}/events?limit=30")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # 30 most recent are present (rev-39..rev-10), older (rev-9..rev-0) are not.
        self.assertIn("rev-39.txt", body)
        self.assertIn("rev-10.txt", body)
        self.assertNotIn("rev-9.txt", body)
        # Load more button still present (40 > 30).
        self.assertIn("Load more", body)
        self.assertIn(f"/files/{self.file.uuid}/events?limit=45", body)

    def test_load_more_button_disappears_when_all_events_loaded(self):
        from workspace.files.services.events import record_event

        for i in range(20):
            record_event(self.file, self.user, FileEvent.Action.RENAMED, {"i": i})

        # Asking for more than total should drop the button.
        response = self.client.get(f"/files/{self.file.uuid}/events?limit=30")

        self.assertNotIn("Load more", response.content.decode())

    def test_endpoint_caps_limit_at_max(self):
        from workspace.files.services.events import record_event

        for i in range(5):
            record_event(self.file, self.user, FileEvent.Action.RENAMED, {"i": i})

        # 99999 must be clamped down to MAX_EVENTS_LIMIT (200) - no 500.
        response = self.client.get(f"/files/{self.file.uuid}/events?limit=99999")

        self.assertEqual(response.status_code, 200)

    def test_refresh_button_renders_with_current_limit_and_filter(self):
        # The refresh button must keep the user's current view (limit +
        # filter) rather than resetting to the default - clicking it after
        # loading 30 renamed events should re-fetch 30 renamed events.
        from workspace.files.services.events import record_event

        for i in range(40):
            record_event(
                self.file,
                self.user,
                FileEvent.Action.RENAMED,
                {
                    "old_name": "a.txt",
                    "new_name": f"rev-{i}.txt",
                },
            )

        response = self.client.get(
            f"/files/{self.file.uuid}/events?limit=30&action=renamed"
        )
        body = response.content.decode()

        self.assertIn('data-lucide="refresh-cw"', body)
        # Django auto-escapes the single quotes (&#x27;) and the ampersand
        # (&amp;) in the :href attribute. The browser decodes them before
        # Alpine evaluates the expression as a JS string literal.
        self.assertIn(
            f"&#x27;/files/{self.file.uuid}/events?limit=30&amp;action=renamed&#x27;",
            body,
        )

    def test_load_more_button_replaced_by_explainer_at_cap(self):
        # When events_limit hits the server cap (200) but there are still
        # more events on the file, the "Load more" button would loop without
        # progress - it must be hidden and an explainer rendered instead.
        from workspace.files.services.events import record_event

        # Seed 250 events so 200 (cap) < total -> explainer branch fires.
        for i in range(250):
            record_event(self.file, self.user, FileEvent.Action.RENAMED, {"i": i})

        response = self.client.get(f"/files/{self.file.uuid}/events?limit=200")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Load more", body)
        self.assertIn("Showing the 200 most recent of 250 events", body)

    def test_endpoint_invalid_limit_falls_back_to_default(self):
        from workspace.files.services.events import record_event

        record_event(self.file, self.user, FileEvent.Action.RENAMED)

        response = self.client.get(f"/files/{self.file.uuid}/events?limit=garbage")

        self.assertEqual(response.status_code, 200)

    def test_endpoint_404s_for_unknown_file(self):
        response = self.client.get("/files/00000000-0000-0000-0000-000000000000/events")
        self.assertEqual(response.status_code, 404)

    def test_endpoint_404s_for_user_without_access(self):
        from workspace.files.services.events import record_event

        record_event(self.file, self.user, FileEvent.Action.RENAMED)
        self.client.logout()
        self.client.login(username="bob", password="pass")

        response = self.client.get(f"/files/{self.file.uuid}/events")

        self.assertEqual(response.status_code, 404)

    def test_filter_by_action_returns_only_matching_events(self):
        from workspace.files.services.events import record_event

        record_event(self.file, self.user, FileEvent.Action.CREATED)
        record_event(
            self.file,
            self.user,
            FileEvent.Action.RENAMED,
            {
                "old_name": "a.txt",
                "new_name": "b.txt",
            },
        )
        record_event(
            self.file,
            self.user,
            FileEvent.Action.SHARED,
            {
                "shared_with_username": "bob",
                "permission": "ro",
            },
        )

        response = self.client.get(f"/files/{self.file.uuid}/events?action=renamed")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # Only the rename event row is rendered.
        self.assertIn("b.txt", body)
        self.assertNotIn("shared with", body)

    def test_filter_for_action_not_on_file_falls_back_to_all(self):
        from workspace.files.services.events import record_event

        # File only has a CREATED event - the dropdown will only offer
        # "Created" as a choice, so a stale URL pointing at "shared"
        # silently resolves to "All actions" rather than rendering an
        # empty timeline.
        record_event(self.file, self.user, FileEvent.Action.CREATED)

        response = self.client.get(f"/files/{self.file.uuid}/events?action=shared")
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        # All-actions option is the selected one, not a phantom 'shared'.
        self.assertIn('value="" selected', body)
        # The CREATED event is still rendered (filter dropped silently).
        self.assertIn("created this", body)

    def test_dropdown_only_offers_actions_present_on_file(self):
        from workspace.files.services.events import record_event

        record_event(self.file, self.user, FileEvent.Action.CREATED)
        record_event(
            self.file,
            self.user,
            FileEvent.Action.RENAMED,
            {
                "old_name": "a.txt",
                "new_name": "b.txt",
            },
        )

        response = self.client.get(f"/files/{self.file.uuid}/events")
        body = response.content.decode()

        # Created and Renamed appear; Shared, Trashed etc. don't.
        self.assertIn('value="created"', body)
        self.assertIn('value="renamed"', body)
        self.assertNotIn('value="shared"', body)
        self.assertNotIn('value="deleted"', body)
        # Lifecycle and Edits optgroups are rendered, Sharing isn't.
        self.assertIn('label="Lifecycle"', body)
        self.assertIn('label="Edits"', body)
        self.assertNotIn('label="Sharing"', body)

    def test_filter_value_preserved_in_dropdown_after_swap(self):
        from workspace.files.services.events import record_event

        record_event(
            self.file,
            self.user,
            FileEvent.Action.RENAMED,
            {
                "old_name": "a.txt",
                "new_name": "b.txt",
            },
        )

        response = self.client.get(f"/files/{self.file.uuid}/events?action=renamed")
        body = response.content.decode()

        # The rename option keeps the selected attribute so the user sees
        # which filter is currently active.
        self.assertIn('value="renamed" selected', body)
        self.assertNotIn('value="" selected', body)

    def test_filter_carried_over_in_load_more_url(self):
        from workspace.files.services.events import record_event

        for i in range(20):
            record_event(
                self.file,
                self.user,
                FileEvent.Action.RENAMED,
                {
                    "old_name": "a.txt",
                    "new_name": f"rev-{i}.txt",
                },
            )
        record_event(self.file, self.user, FileEvent.Action.SHARED)

        response = self.client.get(f"/files/{self.file.uuid}/events?action=renamed")
        body = response.content.decode()

        # Load-more URL preserves the active filter so paginating doesn't
        # silently drop it. Django auto-escapes the ``&`` separator in
        # the rendered href; the browser decodes it before navigation.
        self.assertIn(
            f"/files/{self.file.uuid}/events?limit=30&amp;action=renamed", body
        )

    def test_invalid_filter_value_falls_back_to_all(self):
        from workspace.files.services.events import record_event

        record_event(
            self.file,
            self.user,
            FileEvent.Action.RENAMED,
            {
                "old_name": "a.txt",
                "new_name": "b.txt",
            },
        )

        response = self.client.get(f"/files/{self.file.uuid}/events?action=garbage")

        self.assertEqual(response.status_code, 200)
        # Garbage param is silently treated as "All" so the rename is shown.
        self.assertIn("b.txt", response.content.decode())


class FileEventCopyTests(APITestCase):
    """POST /copy emits CREATED on the new file with source_uuid metadata."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="alice",
            email="a@test.com",
            password="pass",
        )
        self.client.force_authenticate(user=self.user)
        self.file = File.objects.create(
            owner=self.user,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
        )
        self.file.content = ContentFile(b"x", name="doc.txt")
        self.file.size = 1
        self.file.save()
        FileEvent.objects.all().delete()

    def test_copy_emits_created_with_source_uuid(self):
        response = self.client.post(
            f"/api/v1/files/{self.file.uuid}/copy",
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_uuid = response.json()["uuid"]
        self.assertNotEqual(new_uuid, str(self.file.uuid))

        events = list(FileEvent.objects.filter(file__uuid=new_uuid))
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.action, FileEvent.Action.CREATED)
        self.assertEqual(ev.metadata["source_uuid"], str(self.file.uuid))
        self.assertEqual(ev.metadata["source_name"], "doc.txt")

        # The source file is unaffected.
        self.assertEqual(FileEvent.objects.filter(file=self.file).count(), 0)
