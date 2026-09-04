from importlib import import_module

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.tests.migrations import schema_editor_stub
from workspace.projects.models import Project
from workspace.projects.services.projects import (
    create_project,
    get_or_create_personal_project,
)

rekey = import_module(
    "workspace.projects.migrations.0014_rekey_personal_projects"
).rekey

User = get_user_model()


class RekeyPersonalProjectsTests(TestCase):
    """The migration runs against rows created before the username-derived
    key existed, so each test recreates that legacy state by forcing the
    keys back to the PERS, PERS2, PERS3... sequence."""

    def setUp(self):
        self.pierre = User.objects.create_user(
            username="pierre.chopinet", email="p@test.com", password="pass123"
        )
        self.jaaj = User.objects.create_user(
            username="jaaj", email="j@test.com", password="pass123"
        )

    def _simulate_legacy_keys(self):
        personal = Project.objects.filter(type=Project.Type.PERSONAL).order_by(
            "created_at", "uuid"
        )
        for i, pk in enumerate(personal.values_list("pk", flat=True)):
            legacy = "PERS" if i == 0 else f"PERS{i + 1}"
            Project.objects.filter(pk=pk).update(key=legacy)

    def test_keys_are_rederived_from_usernames(self):
        first = get_or_create_personal_project(self.pierre)
        second = get_or_create_personal_project(self.jaaj)
        self._simulate_legacy_keys()

        rekey(apps, schema_editor_stub())

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.key, second.key), ("PERSPC", "PERSJAAJ"))

    def test_non_personal_projects_keep_their_keys(self):
        board = create_project(self.pierre, name="Website Redesign")
        get_or_create_personal_project(self.pierre)
        self._simulate_legacy_keys()

        rekey(apps, schema_editor_stub())

        board.refresh_from_db()
        self.assertEqual(board.key, "WR")

    def test_collision_with_an_existing_key_gets_a_suffix(self):
        create_project(self.pierre, name="Personal Cabinet")
        personal = get_or_create_personal_project(self.pierre)
        self._simulate_legacy_keys()
        self.assertEqual(Project.objects.get(name="Personal Cabinet").key, "PC")
        Project.objects.filter(name="Personal Cabinet").update(key="PERSPC")

        rekey(apps, schema_editor_stub())

        personal.refresh_from_db()
        self.assertEqual(personal.key, "PERSPC2")

    def test_owner_less_project_keeps_the_bare_prefix(self):
        personal = get_or_create_personal_project(self.pierre)
        self._simulate_legacy_keys()
        Project.objects.filter(pk=personal.pk).update(created_by=None)

        rekey(apps, schema_editor_stub())

        personal.refresh_from_db()
        self.assertEqual(personal.key, "PERS")

    def test_is_idempotent(self):
        get_or_create_personal_project(self.pierre)
        get_or_create_personal_project(self.jaaj)
        self._simulate_legacy_keys()

        rekey(apps, schema_editor_stub())
        keys_after_first_run = sorted(
            Project.objects.filter(type=Project.Type.PERSONAL).values_list(
                "key", flat=True
            )
        )
        rekey(apps, schema_editor_stub())

        self.assertEqual(
            sorted(
                Project.objects.filter(type=Project.Type.PERSONAL).values_list(
                    "key", flat=True
                )
            ),
            keys_after_first_run,
        )
