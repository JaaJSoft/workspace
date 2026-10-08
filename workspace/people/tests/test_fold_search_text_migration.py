from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.common.tests.migrations import schema_editor_stub
from workspace.people.models import Person
from workspace.people.services.persons import create_person

migration = import_module("workspace.people.migrations.0003_fold_person_search_text")

User = get_user_model()


class FoldSearchTextMigrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="x")

    def test_rebuilds_a_row_saved_with_accents(self):
        person = create_person(owner=self.user, display_name="Hélène Dupré")
        Person.objects.filter(pk=person.pk).update(search_text="hélène dupré")

        migration.fold_search_text(django_apps, schema_editor_stub())

        person.refresh_from_db()
        self.assertEqual(person.search_text, "helene dupre")

    def test_matches_what_a_save_writes_today(self):
        person = create_person(
            owner=self.user,
            display_name="Zoé Straße",
            organization="Crédit",
            emails=[{"value": "Zoe@Example.com", "type": "work"}],
            phones=[{"value": "+33 6 12", "type": "cell"}],
        )
        saved = person.search_text
        Person.objects.filter(pk=person.pk).update(search_text="")

        migration.fold_search_text(django_apps, schema_editor_stub())

        person.refresh_from_db()
        self.assertEqual(person.search_text, saved)
