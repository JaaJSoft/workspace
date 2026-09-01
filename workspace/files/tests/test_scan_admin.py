from unittest.mock import patch

from django.contrib.admin import site
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from workspace.core.services.admin_dashboard import (
    quarantined_file_badge,
    quarantined_file_count,
    scanner_error_count,
    scanner_health_card,
)
from workspace.files.models import File, FileScan
from workspace.files.services.scanning.base import ScannerHealth

User = get_user_model()
BLOCKING = {
    "FILES_MALWARE_SCAN_ENABLED": True,
    "FILES_MALWARE_ON_DETECTION": "block",
}


class AdminCountTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="adm", password="p")
        self.request = RequestFactory().get("/admin/")
        self._scan("a.txt", FileScan.Status.INFECTED)
        self._scan("b.txt", FileScan.Status.INFECTED)
        self._scan("c.txt", FileScan.Status.ERROR)
        self._scan("d.txt", FileScan.Status.CLEAN)
        self._scan("e.txt", FileScan.Status.SKIPPED)

    def _scan(self, name, status, scanned_at=None):
        f = File.objects.create(
            owner=self.user, name=name, node_type=File.NodeType.FILE
        )
        return FileScan.objects.create(
            file=f, status=status, scanned_at=scanned_at or timezone.now()
        )

    @override_settings(**BLOCKING)
    def test_quarantined_count_is_the_blocked_rows(self):
        self.assertEqual(quarantined_file_count(self.request), 2)

    @override_settings(
        FILES_MALWARE_SCAN_ENABLED=True,
        FILES_MALWARE_ON_DETECTION="block",
        FILES_MALWARE_ON_ERROR="closed",
    )
    def test_fail_closed_counts_errors_as_quarantined(self):
        self.assertEqual(quarantined_file_count(self.request), 3)

    @override_settings(
        FILES_MALWARE_SCAN_ENABLED=True, FILES_MALWARE_ON_DETECTION="flag"
    )
    def test_flag_policy_quarantines_nothing(self):
        self.assertEqual(quarantined_file_count(self.request), 0)

    @override_settings(FILES_MALWARE_SCAN_ENABLED=False)
    def test_disabled_scanning_counts_nothing(self):
        self.assertEqual(quarantined_file_count(self.request), 0)

    @override_settings(**BLOCKING)
    def test_error_count_is_scoped_to_24h(self):
        from datetime import timedelta

        old = self._scan("old.txt", FileScan.Status.ERROR)
        FileScan.objects.filter(pk=old.pk).update(
            scanned_at=timezone.now() - timedelta(days=2)
        )
        self.assertEqual(scanner_error_count(self.request), 1)

    @override_settings(**BLOCKING)
    def test_badge_hides_a_zero(self):
        FileScan.objects.all().delete()
        self.assertIsNone(quarantined_file_badge(self.request))

    @override_settings(**BLOCKING)
    def test_badge_shows_a_nonzero_count(self):
        self.assertEqual(quarantined_file_badge(self.request), 2)


class ScannerHealthCardTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/admin/")
        from django.core.cache import cache

        cache.clear()

    def tearDown(self):
        from django.core.cache import cache

        cache.clear()

    @override_settings(FILES_MALWARE_SCAN_ENABLED=False)
    def test_no_card_when_scanning_is_disabled(self):
        self.assertIsNone(scanner_health_card(self.request))

    @override_settings(**BLOCKING)
    def test_reachable_scanner_is_a_healthy_card(self):
        class _Stub:
            def health(self):
                return ScannerHealth(reachable=True, version="ClamAV 1.4.1")

            def scan(self, stream, *, name=""):
                raise AssertionError("not used here")

        with patch(
            "workspace.files.services.scanning.registry.get_scanner",
            return_value=_Stub(),
        ):
            card = scanner_health_card(self.request)
        self.assertEqual(card["tone"], "success")
        self.assertIn("ClamAV", card["value"])

    @override_settings(**BLOCKING)
    def test_unreachable_scanner_is_a_danger_card(self):
        class _Stub:
            def health(self):
                return ScannerHealth(reachable=False, error="connection refused")

            def scan(self, stream, *, name=""):
                raise AssertionError("not used here")

        with patch(
            "workspace.files.services.scanning.registry.get_scanner",
            return_value=_Stub(),
        ):
            card = scanner_health_card(self.request)
        self.assertEqual(card["tone"], "danger")

    @override_settings(**BLOCKING)
    def test_health_is_cached_so_a_dead_daemon_costs_one_probe_a_minute(self):
        calls = []

        class _Stub:
            def health(self):
                calls.append(1)
                return ScannerHealth(reachable=True, version="ClamAV 1.4.1")

            def scan(self, stream, *, name=""):
                raise AssertionError("not used here")

        with patch(
            "workspace.files.services.scanning.registry.get_scanner",
            return_value=_Stub(),
        ):
            scanner_health_card(self.request)
            scanner_health_card(self.request)
        self.assertEqual(len(calls), 1)


class FileScanAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="p"
        )
        self.client.force_login(self.admin)
        user = User.objects.create_user(username="own", password="p")
        f = File.objects.create(
            owner=user, name="bad.txt", node_type=File.NodeType.FILE
        )
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at=timezone.now(),
        )

    def test_changelist_renders(self):
        resp = self.client.get("/admin/files/filescan/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Unit.Test")


class FileScanAdminDeletionTests(TestCase):
    """A verdict cannot be deleted away; deleting one would un-quarantine."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="delroot", email="delroot@example.com", password="p"
        )
        self.client.force_login(self.admin_user)
        owner = User.objects.create_user(username="delowner", password="p")
        self.file = File.objects.create(
            owner=owner, name="bad.txt", node_type=File.NodeType.FILE
        )
        self.scan = FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Unit.Test",
            scanned_at=timezone.now(),
        )

    def test_a_superuser_cannot_delete_an_infected_verdict(self):
        resp = self.client.post(
            f"/admin/files/filescan/{self.scan.pk}/delete/", {"post": "yes"}
        )
        self.assertIn(resp.status_code, (403, 302))
        self.assertTrue(FileScan.objects.filter(pk=self.scan.pk).exists())

    def test_the_bulk_delete_action_is_not_offered(self):
        resp = self.client.get("/admin/files/filescan/")
        self.assertNotContains(resp, 'value="delete_selected"')

    def test_the_rescan_action_queues_a_fresh_scan_and_keeps_the_row(self):
        with patch("workspace.files.tasks.scan_file.delay") as delay:
            resp = self.client.post(
                "/admin/files/filescan/",
                {
                    "action": "rescan_files",
                    "_selected_action": [str(self.scan.pk)],
                },
                follow=True,
            )
        self.assertEqual(resp.status_code, 200)
        delay.assert_called_once_with(str(self.file.uuid))
        self.assertTrue(FileScan.objects.filter(pk=self.scan.pk).exists())


class FileScanAdminCurrencyTests(TestCase):
    """The changelist answers "does this verdict still describe this file?"."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="curroot", email="curroot@example.com", password="p"
        )
        self.client.force_login(self.admin_user)
        self.owner = User.objects.create_user(username="curowner", password="p")

    def _scanned(self, name, *, stale=False, no_hash=False):
        from django.core.files.base import ContentFile

        from workspace.files.services import FileService

        f = FileService.create_file(
            self.owner, name, content=ContentFile(b"body", name=name)
        )
        recorded = "" if no_hash else ("0" * 64 if stale else f.content_hash)
        FileScan.objects.create(
            file=f,
            status=FileScan.Status.CLEAN,
            content_hash=recorded,
            scanned_at=timezone.now(),
        )
        return f

    def _is_current(self, file_obj):
        from workspace.files.admin import FileScanAdmin

        return FileScanAdmin.is_current(None, file_obj.scan)

    def test_a_verdict_matching_the_file_is_current(self):
        self.assertTrue(self._is_current(self._scanned("fresh.txt")))

    def test_a_verdict_about_other_bytes_is_not_current(self):
        self.assertFalse(self._is_current(self._scanned("stale.txt", stale=True)))

    def test_a_verdict_with_no_recorded_hash_is_not_current(self):
        self.assertFalse(self._is_current(self._scanned("legacy.txt", no_hash=True)))

    def test_the_changelist_renders_the_column(self):
        self._scanned("shown.txt")
        resp = self.client.get("/admin/files/filescan/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Up to date")


BLOCKING_SETTINGS = {
    "FILES_MALWARE_SCAN_ENABLED": True,
    "FILES_MALWARE_ON_DETECTION": "block",
}


@override_settings(**BLOCKING_SETTINGS)
class FileScanAdminOverrideTests(TestCase):
    """Clearing a false positive: the one thing re-scanning cannot do."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="ovroot", email="ovroot@example.com", password="p"
        )
        self.client.force_login(self.admin_user)
        owner = User.objects.create_user(username="ovowner", password="p")
        self.file = File.objects.create(
            owner=owner,
            name="report.doc",
            node_type=File.NodeType.FILE,
            content_hash="h1",
        )
        self.scan = FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Heuristics.False.Positive",
            content_hash="h1",
            scanned_at=timezone.now(),
        )

    def _run_action(self, **extra):
        return self.client.post(
            "/admin/files/filescan/",
            {
                "action": "mark_safe",
                "_selected_action": [str(self.scan.pk)],
                **extra,
            },
            follow=True,
        )

    def test_the_action_lifts_the_quarantine(self):
        from workspace.files.services.scanning.policy import is_blocked

        self._run_action()

        self.file.refresh_from_db()
        self.assertFalse(is_blocked(self.file))

    def test_the_action_records_who_cleared_it_and_why(self):
        self._run_action(override_reason="Confirmed benign with the vendor")

        self.scan.refresh_from_db()
        self.assertEqual(self.scan.overridden_by, self.admin_user)
        self.assertEqual(self.scan.override_reason, "Confirmed benign with the vendor")

    def test_the_action_keeps_the_verdict_it_overrides(self):
        self._run_action()

        self.scan.refresh_from_db()
        self.assertEqual(self.scan.status, FileScan.Status.INFECTED)
        self.assertEqual(self.scan.signature, "Heuristics.False.Positive")

    def test_a_verdict_about_older_bytes_is_refused_with_a_warning(self):
        File.objects.filter(pk=self.file.pk).update(content_hash="h2")

        resp = self._run_action()

        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.overridden_at)
        self.assertContains(resp, "Re-scan those files first")

    def test_the_action_select_keeps_its_alpine_binding(self):
        """Without it the Run button never appears and no action can run.

        Unfold gates that button on ``x-show="action"``, and only its own
        ActionForm widget carries the matching ``x-model``. Subclassing
        Django's plain ActionForm instead silently disables every action on
        this changelist - the POST still works, so no test that posts
        directly would notice.
        """
        resp = self.client.get("/admin/files/filescan/")
        self.assertContains(resp, 'x-model="action"')

    def test_the_changelist_offers_a_reason_field(self):
        """Unfold renders the action bar itself; a silently dropped ActionForm
        would leave every clearance unexplained in the audit trail."""
        resp = self.client.get("/admin/files/filescan/")
        self.assertContains(resp, 'name="override_reason"')

    def test_a_staff_user_without_the_permission_cannot_clear_a_quarantine(self):
        viewer = User.objects.create_user(
            username="ovviewer", password="p", is_staff=True
        )
        viewer.user_permissions.add(Permission.objects.get(codename="view_filescan"))
        self.client.force_login(viewer)

        self._run_action()

        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.overridden_at)

    def test_the_permission_is_enough_on_its_own(self):
        clearer = User.objects.create_user(
            username="ovclearer", password="p", is_staff=True
        )
        clearer.user_permissions.add(
            Permission.objects.get(codename="view_filescan"),
            Permission.objects.get(codename="override_filescan"),
        )
        self.client.force_login(clearer)

        self._run_action()

        self.scan.refresh_from_db()
        self.assertIsNotNone(self.scan.overridden_at)


@override_settings(**BLOCKING_SETTINGS)
class FileScanAdminRowActionTests(TestCase):
    """Clearing one row without touching the bulk-selection machinery."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="rowroot", email="rowroot@example.com", password="p"
        )
        self.client.force_login(self.admin_user)
        owner = User.objects.create_user(username="rowowner", password="p")
        self.file = File.objects.create(
            owner=owner,
            name="report.doc",
            node_type=File.NodeType.FILE,
            content_hash="h1",
        )
        self.scan = FileScan.objects.create(
            file=self.file,
            status=FileScan.Status.INFECTED,
            signature="Heuristics.False.Positive",
            content_hash="h1",
            scanned_at=timezone.now(),
        )
        self.url = f"/admin/files/filescan/{self.scan.pk}/clear-quarantine/"

    def test_system_checks_do_not_touch_the_database(self):
        """Checks run before migrations do, on a schema that does not exist yet.

        unfold validates a dotted `app.codename` action permission with a
        query against auth_permission, so declaring one that way makes
        `manage.py migrate` crash on a fresh database - every first deploy.
        The method form (`permissions=["override"]`) is checked against the
        ModelAdmin instead, without a query.
        """
        model_admin = site._registry[FileScan]

        with self.assertNumQueries(0):
            errors = model_admin.check()

        self.assertEqual(errors, [])

    def test_the_changelist_renders_the_row_button(self):
        """Posting to the URL is not proof the operator can reach it.

        The bulk action shipped broken once because every test took the POST
        path a real click never takes; this asserts the button is on the page.
        """
        resp = self.client.get("/admin/files/filescan/")
        self.assertContains(resp, f"{self.scan.pk}/clear-quarantine/")

    def test_a_get_opens_the_dialog_without_clearing_anything(self):
        resp = self.client.get(self.url)

        self.assertEqual(resp.status_code, 200)
        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.overridden_at)

    def test_submitting_the_dialog_clears_the_quarantine(self):
        from workspace.files.services.scanning.policy import is_blocked

        resp = self.client.post(
            self.url,
            {"_form_submitted": "1", "override_reason": "Confirmed benign"},
            follow=True,
        )

        self.assertEqual(resp.status_code, 200)
        self.scan.refresh_from_db()
        self.assertEqual(self.scan.overridden_by, self.admin_user)
        self.assertEqual(self.scan.override_reason, "Confirmed benign")
        self.file.refresh_from_db()
        self.assertFalse(is_blocked(self.file))

    def test_the_htmx_submit_tells_the_browser_to_navigate(self):
        """The dialog posts through htmx, which swaps the response into the
        modal instead of following a redirect - so the operator would sit on a
        spinning dialog while the clearance had already happened. Only an
        HX-Redirect header gets the page back to the changelist."""
        resp = self.client.post(
            self.url,
            {"_form_submitted": "1", "override_reason": "x"},
            headers={"hx-request": "true"},
        )

        self.assertEqual(resp.headers.get("HX-Redirect"), "/admin/files/filescan/")

    def test_it_keeps_the_verdict_it_overrides(self):
        self.client.post(
            self.url, {"_form_submitted": "1", "override_reason": "x"}, follow=True
        )

        self.scan.refresh_from_db()
        self.assertEqual(self.scan.status, FileScan.Status.INFECTED)
        self.assertEqual(self.scan.signature, "Heuristics.False.Positive")

    def test_a_staff_user_without_the_permission_is_refused(self):
        viewer = User.objects.create_user(
            username="rowviewer", password="p", is_staff=True
        )
        viewer.user_permissions.add(Permission.objects.get(codename="view_filescan"))
        self.client.force_login(viewer)

        resp = self.client.post(
            self.url, {"_form_submitted": "1", "override_reason": "x"}
        )

        self.assertEqual(resp.status_code, 403)
        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.overridden_at)

    def test_a_verdict_about_older_bytes_is_refused_with_a_warning(self):
        File.objects.filter(pk=self.file.pk).update(content_hash="h2")

        resp = self.client.post(
            self.url, {"_form_submitted": "1", "override_reason": "x"}, follow=True
        )

        self.scan.refresh_from_db()
        self.assertIsNone(self.scan.overridden_at)
        self.assertContains(resp, "Re-scan it first")


@override_settings(**BLOCKING_SETTINGS)
class FileScanAdminQuarantineColumnTests(TestCase):
    """The changelist answers "what is blocked right now?", not "what was scanned"."""

    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="colroot", email="colroot@example.com", password="p"
        )
        self.client.force_login(self.admin_user)
        self.owner = User.objects.create_user(username="colowner", password="p")

    def _scan(self, name, status, **fields):
        f = File.objects.create(
            owner=self.owner,
            name=name,
            node_type=File.NodeType.FILE,
            content_hash="h1",
        )
        return FileScan.objects.create(
            file=f,
            status=status,
            content_hash="h1",
            scanned_at=timezone.now(),
            **fields,
        )

    def test_a_blocking_verdict_reads_as_quarantined(self):
        self._scan("bad.txt", FileScan.Status.INFECTED)

        self.assertContains(self.client.get("/admin/files/filescan/"), "Quarantined")

    def test_a_cleared_verdict_reads_as_cleared(self):
        self._scan(
            "ok.txt",
            FileScan.Status.INFECTED,
            overridden_at=timezone.now(),
            overridden_by=self.admin_user,
        )

        resp = self.client.get("/admin/files/filescan/")
        self.assertContains(resp, "Cleared")
        self.assertNotContains(resp, "Quarantined")

    def test_a_clean_verdict_reads_as_neither(self):
        self._scan("fine.txt", FileScan.Status.CLEAN)

        resp = self.client.get("/admin/files/filescan/")
        self.assertNotContains(resp, "Quarantined")
        self.assertNotContains(resp, "Cleared")
