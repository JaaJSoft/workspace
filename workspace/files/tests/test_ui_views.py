from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from workspace.files.models import File, FileShareLink
from workspace.files.ui.views import SHARED_FOLDER_PAGE_SIZE
from workspace.users.services.settings import set_setting

User = get_user_model()


class FilesIndexSettingsTests(TestCase):
    """The file browser view reads per-user 'preferences' and 'viewer'
    settings to populate its context. Both live in the ``files`` module, so
    they should be fetched in a single query, not one per key."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="prefs_user",
            email="prefs@test.com",
            password="x",
        )
        self.client.force_login(self.user)

    def tearDown(self):
        # set_setting populates the process-global LocMemCache, which is not
        # reset between TestCase runs. Clear it to keep tests order-independent.
        cache.clear()

    def test_index_exposes_file_and_viewer_preferences(self):
        """Both settings flow into the template context with the right values."""
        set_setting(self.user, "files", "preferences", {"breadcrumbCollapse": 2})
        set_setting(self.user, "files", "viewer", {"theme": "dark"})

        response = self.client.get(reverse("files_ui:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["breadcrumb_collapse"], 2)
        self.assertEqual(response.context["file_prefs"], {"breadcrumbCollapse": 2})
        self.assertEqual(response.context["viewer_prefs"], {"theme": "dark"})

    def test_index_defaults_when_settings_absent(self):
        """Missing settings fall back to the documented defaults."""
        response = self.client.get(reverse("files_ui:index"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["breadcrumb_collapse"], 4)
        self.assertEqual(response.context["file_prefs"], {})
        self.assertEqual(response.context["viewer_prefs"], {})

    def test_index_loads_files_settings_in_a_single_query(self):
        """Cold cache: the two files settings are read in one DB round-trip."""
        set_setting(self.user, "files", "preferences", {"breadcrumbCollapse": 3})
        set_setting(self.user, "files", "viewer", {"theme": "light"})
        # Cold the cache so reads hit the database.
        cache.clear()

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse("files_ui:index"))

        self.assertEqual(response.status_code, 200)
        # Only count reads scoped to the files module; the core context
        # processors issue their own usersetting reads (theme, timezone, ...)
        # which are out of scope here.
        setting_queries = [
            q["sql"]
            for q in ctx.captured_queries
            if "users_usersetting" in q["sql"] and "\"module\" = 'files'" in q["sql"]
        ]
        self.assertEqual(
            len(setting_queries),
            1,
            f"expected a single files users_usersetting query, got "
            f"{len(setting_queries)}:\n" + "\n".join(setting_queries),
        )


class SharedLinkPageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="pageowner", email="pageowner@example.com", password="pass123"
        )
        self.doc = File.objects.create(
            owner=self.owner,
            name="doc.txt",
            node_type=File.NodeType.FILE,
            mime_type="text/plain",
            type="text",
            category="text",
        )
        self.link = FileShareLink.objects.create(file=self.doc, created_by=self.owner)

    def test_a_file_link_renders_the_file_page(self):
        resp = self.client.get(f"/files/shared/{self.link.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "doc.txt")

    def test_an_expired_link_renders_the_expired_card(self):
        self.link.expires_at = timezone.now() - timedelta(days=1)
        self.link.save(update_fields=["expires_at"])
        resp = self.client.get(f"/files/shared/{self.link.token}")
        self.assertContains(resp, "Link expired")

    def test_a_password_protected_link_renders_the_prompt(self):
        self.link.password = make_password("secret")
        self.link.save(update_fields=["password"])
        resp = self.client.get(f"/files/shared/{self.link.token}")
        self.assertContains(resp, "Enter the password")

    def test_a_file_link_names_who_shared_it(self):
        """A recipient should know whose file this is, as on the folder pages."""
        resp = self.client.get(f"/files/shared/{self.link.token}")
        self.assertContains(resp, self.owner.username)

    def test_an_unknown_token_is_a_404(self):
        self.assertEqual(self.client.get("/files/shared/nope").status_code, 404)

    def test_a_trashed_target_is_a_404(self):
        self.doc.soft_delete()
        resp = self.client.get(f"/files/shared/{self.link.token}")
        self.assertEqual(resp.status_code, 404)

    def test_a_file_link_records_a_view(self):
        resp = self.client.get(f"/files/shared/{self.link.token}")
        self.assertEqual(resp.status_code, 200)
        self.link.refresh_from_db()
        self.assertEqual(self.link.view_count, 1)
        self.assertIsNotNone(self.link.last_accessed_at)

    def test_a_file_link_has_no_breadcrumb(self):
        """The target IS the link's own root - there is nowhere to browse
        from, so the page renders no breadcrumb trail at all."""
        resp = self.client.get(f"/files/shared/{self.link.token}")
        self.assertIsNone(resp.context["breadcrumbs"])
        self.assertNotIn(b"<nav", resp.content)

    def test_a_read_mode_folder_link_renders_the_browse_page(self):
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        link = FileShareLink.objects.create(
            file=folder, created_by=self.owner, mode=FileShareLink.Mode.READ
        )
        resp = self.client.get(f"/files/shared/{link.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "files/ui/shared_page.html")
        self.assertTemplateUsed(resp, "files/ui/partials/shared_listing.html")
        self.assertContains(resp, self.owner.username)

    def test_a_both_mode_folder_link_renders_the_browse_page_with_a_dropzone(self):
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        link = FileShareLink.objects.create(
            file=folder, created_by=self.owner, mode=FileShareLink.Mode.BOTH
        )
        resp = self.client.get(f"/files/shared/{link.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "files/ui/partials/shared_listing.html")
        self.assertTemplateUsed(resp, "files/ui/partials/shared_dropzone.html")
        self.assertContains(resp, self.owner.username)

    def test_the_dropzone_is_outside_the_navigation_fragment(self):
        """The dropzone always uploads into the link's own root, never the
        subfolder being browsed, so it must survive a swap instead of being
        torn down and rebuilt (losing its queue) on every navigation."""
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        link = FileShareLink.objects.create(
            file=folder, created_by=self.owner, mode=FileShareLink.Mode.BOTH
        )

        fragment = self.client.get(
            f"/files/shared/{link.token}", HTTP_X_ALPINE_REQUEST="true"
        )
        self.assertEqual(fragment.status_code, 200)
        self.assertNotContains(fragment, 'data-testid="drop-zone"')

        full_page = self.client.get(f"/files/shared/{link.token}")
        self.assertEqual(full_page.status_code, 200)
        self.assertContains(full_page, 'data-testid="drop-zone"')

    def test_the_dropzone_names_the_share_root_while_browsing_a_subfolder(self):
        """Uploads always land in the share root (SharedFolderUploadView
        never takes a target folder), so the dropzone must keep naming that
        root - not the subfolder currently on screen - or the interface
        misleads the visitor about where their files are going."""
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        sub = File.objects.create(
            owner=self.owner,
            name="Sub",
            node_type=File.NodeType.FOLDER,
            parent=folder,
        )
        link = FileShareLink.objects.create(
            file=folder, created_by=self.owner, mode=FileShareLink.Mode.BOTH
        )

        resp = self.client.get(f"/files/shared/{link.token}", {"node": str(sub.uuid)})

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'data-testid="drop-zone"')
        self.assertContains(resp, "Send files to Docs")
        self.assertNotContains(resp, "Send files to Sub")

    def test_a_drop_mode_folder_link_renders_the_drop_page_with_no_listing(self):
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        link = FileShareLink.objects.create(
            file=folder, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )
        resp = self.client.get(f"/files/shared/{link.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "files/ui/partials/shared_dropzone.html")
        # The write-only guarantee expressed at the template layer: no
        # listing partial, no viewer partial, no breadcrumb.
        self.assertTemplateNotUsed(resp, "files/ui/partials/shared_listing.html")
        self.assertTemplateNotUsed(resp, "files/ui/partials/shared_viewer.html")
        self.assertIsNone(resp.context["breadcrumbs"])
        self.assertNotContains(resp, 'id="shared-listing"')
        # A visitor must know who they're sending files to.
        self.assertContains(resp, self.owner.username)

    def test_a_drop_mode_folder_link_never_resolves_node(self):
        """?node= must be a no-op on a drop link, not a 404 or a leak."""
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        hidden = File.objects.create(
            owner=self.owner,
            name="hidden.txt",
            node_type=File.NodeType.FILE,
            parent=folder,
        )
        link = FileShareLink.objects.create(
            file=folder, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )
        resp = self.client.get(
            f"/files/shared/{link.token}", {"node": str(hidden.uuid)}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "hidden.txt")
        self.assertTemplateUsed(resp, "files/ui/partials/shared_dropzone.html")

    def test_a_password_protected_drop_link_page_hides_the_owner_until_verified(self):
        """The rendered page withholds the owner's name pre-verification.

        This is a page-rendering guarantee only: the meta endpoint
        (``GET /api/v1/files/shared/{token}``) deliberately still returns
        ``created_by_name`` with no password check at all, by pre-existing
        design. The owner's name is not a secret; it is just not painted on
        the password screen.
        """
        folder = File.objects.create(
            owner=self.owner, name="Docs", node_type=File.NodeType.FOLDER
        )
        link = FileShareLink.objects.create(
            file=folder,
            created_by=self.owner,
            mode=FileShareLink.Mode.DROP,
            password=make_password("secret"),
        )
        resp = self.client.get(f"/files/shared/{link.token}")
        self.assertContains(resp, "Enter the password")
        self.assertNotContains(resp, self.owner.username)


class SharedFolderListingTests(TestCase):
    """Server-rendered folder listing on the public share page.

    Ports the guarantees previously pinned on ``GET .../entries`` (removed
    along with the client-rendered browser) onto the page render itself.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username="listingowner",
            email="listingowner@example.com",
            password="pass123",
        )
        self.other = User.objects.create_user(
            username="listingother",
            email="listingother@example.com",
            password="pass123",
        )
        self.root = File.objects.create(
            owner=self.owner, name="Shared", node_type=File.NodeType.FOLDER
        )
        self.sub = File.objects.create(
            owner=self.owner,
            name="Sub",
            node_type=File.NodeType.FOLDER,
            parent=self.root,
        )
        self.doc = File.objects.create(
            owner=self.owner,
            name="in.txt",
            node_type=File.NodeType.FILE,
            parent=self.sub,
        )
        self.outside = File.objects.create(
            owner=self.owner, name="out.txt", node_type=File.NodeType.FILE
        )
        self.read_link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.READ
        )
        self.drop_link = FileShareLink.objects.create(
            file=self.root, created_by=self.owner, mode=FileShareLink.Mode.DROP
        )

    def test_renders_the_root_listing(self):
        resp = self.client.get(f"/files/shared/{self.read_link.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Sub")
        self.assertNotContains(resp, "out.txt")

    def test_a_fragment_request_for_an_open_link_returns_only_the_content_partial(self):
        """The alpine-ajax swap target must not carry a second copy of the
        page shell - only the shared-content region."""
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}",
            {"node": str(self.sub.uuid)},
            HTTP_X_ALPINE_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="shared-content"')
        self.assertNotContains(resp, "<!DOCTYPE")

    def test_node_param_renders_the_subfolder(self):
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}", {"node": str(self.sub.uuid)}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "in.txt")

    def test_node_param_of_a_file_renders_the_viewer_with_a_breadcrumb(self):
        """A file inside a shared folder behaves like a file shared on its
        own: viewer box, download link, and a breadcrumb back to its
        folder."""
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}", {"node": str(self.doc.uuid)}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "files/ui/partials/shared_viewer.html")
        self.assertTemplateNotUsed(resp, "files/ui/partials/shared_listing.html")
        self.assertIsNotNone(resp.context["breadcrumbs"])
        self.assertContains(resp, "Sub")
        self.assertContains(
            resp,
            f"/api/v1/files/shared/{self.read_link.token}/download?file={self.doc.uuid}",
        )

    def test_a_listing_entry_links_to_its_node(self):
        """Rows navigate through ?node=, whether they name a folder or a
        file - both dead-end at the same page."""
        resp = self.client.get(f"/files/shared/{self.read_link.token}")
        self.assertContains(resp, f"?node={self.sub.uuid}")

    def test_node_param_outside_the_subtree_is_a_404(self):
        stranger = File.objects.create(
            owner=self.owner, name="Elsewhere", node_type=File.NodeType.FOLDER
        )
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}", {"node": str(stranger.uuid)}
        )
        self.assertEqual(resp.status_code, 404)

    def test_node_param_of_another_users_colliding_path_is_a_404(self):
        """`path` text is not globally unique - mirrors ResolveWithinTests."""
        their_root = File.objects.create(
            owner=self.other, name="Shared", node_type=File.NodeType.FOLDER
        )
        their_file = File.objects.create(
            owner=self.other,
            name="secret.txt",
            node_type=File.NodeType.FILE,
            parent=their_root,
        )
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}", {"node": str(their_file.uuid)}
        )
        self.assertEqual(resp.status_code, 404)

    def test_a_trashed_descendant_is_a_404(self):
        self.sub.soft_delete()
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}", {"node": str(self.sub.uuid)}
        )
        self.assertEqual(resp.status_code, 404)

    def test_breadcrumbs_never_go_above_the_share_root(self):
        parent = File.objects.create(
            owner=self.owner, name="TopSecretParent", node_type=File.NodeType.FOLDER
        )
        self.root.parent = parent
        self.root.save(update_fields=["parent"])
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}", {"node": str(self.sub.uuid)}
        )
        self.assertNotContains(resp, "TopSecretParent")

    def test_listing_never_exposes_an_absolute_path(self):
        parent = File.objects.create(
            owner=self.owner, name="TopSecretParent", node_type=File.NodeType.FOLDER
        )
        self.root.parent = parent
        self.root.save(update_fields=["parent"])
        resp = self.client.get(f"/files/shared/{self.read_link.token}")
        self.root.refresh_from_db()
        self.assertNotContains(resp, self.root.path)
        self.assertNotContains(resp, "TopSecretParent")

    def test_a_drop_link_gets_no_listing(self):
        resp = self.client.get(f"/files/shared/{self.drop_link.token}")
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'id="shared-listing"')

    def test_more_children_than_the_page_size_are_truncated(self):
        bulk_root = File.objects.create(
            owner=self.owner, name="Bulk", node_type=File.NodeType.FOLDER
        )
        link = FileShareLink.objects.create(
            file=bulk_root, created_by=self.owner, mode=FileShareLink.Mode.READ
        )
        for index in range(SHARED_FOLDER_PAGE_SIZE + 5):
            File.objects.create(
                owner=self.owner,
                name=f"f{index:04}.txt",
                node_type=File.NodeType.FILE,
                parent=bulk_root,
            )
        resp = self.client.get(f"/files/shared/{link.token}")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertEqual(content.count('title="Download"'), SHARED_FOLDER_PAGE_SIZE)
        self.assertContains(
            resp, f"Only the first {SHARED_FOLDER_PAGE_SIZE} entries are shown."
        )

    def test_password_protected_fragment_request_without_a_token_has_no_listing(self):
        """A raw X-Alpine-Request must not bypass the password gate.

        Nothing stops an attacker sending this header directly at the URL -
        it must be answered exactly like a normal, gate-failing request.
        """
        self.read_link.password = make_password("secret")
        self.read_link.save(update_fields=["password"])
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}",
            HTTP_X_ALPINE_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "Sub")
        self.assertNotContains(resp, 'id="shared-listing"')
        self.assertContains(resp, "Enter the password")

    def test_password_protected_fragment_request_of_a_file_has_no_viewer(self):
        """A ?node= naming a file must not be resolved while locked either -
        the lock is checked before the node is even looked up, so the target
        stays the (folder) root and nothing about the file leaks."""
        self.read_link.password = make_password("secret")
        self.read_link.save(update_fields=["password"])
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}",
            {"node": str(self.doc.uuid)},
            HTTP_X_ALPINE_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "in.txt")
        self.assertNotContains(resp, 'id="shared-viewer"')
        self.assertContains(resp, "Enter the password")

    def test_a_listing_records_a_view(self):
        resp = self.client.get(f"/files/shared/{self.read_link.token}")
        self.assertEqual(resp.status_code, 200)
        self.read_link.refresh_from_db()
        self.assertEqual(self.read_link.view_count, 1)

    def test_viewing_a_descendant_file_records_a_view(self):
        resp = self.client.get(
            f"/files/shared/{self.read_link.token}", {"node": str(self.doc.uuid)}
        )
        self.assertEqual(resp.status_code, 200)
        self.read_link.refresh_from_db()
        self.assertEqual(self.read_link.view_count, 1)
