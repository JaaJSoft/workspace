from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.core.module_registry import registry
from workspace.people.search import search_persons
from workspace.people.services.persons import create_person

User = get_user_model()


class SearchPersonsTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user(username="alice", password="x")
        self.bob = User.objects.create_user(username="bob", password="x")
        self.person = create_person(
            owner=self.alice,
            display_name="Bob Martin",
            emails=[{"value": "bob@acme.com", "type": "work"}],
        )
        create_person(owner=self.bob, display_name="Bob Hidden")

    def test_matches_name(self):
        results = search_persons("mart", self.alice, 10)
        self.assertEqual([r.uuid for r in results], [str(self.person.uuid)])
        self.assertEqual(results[0].matched_value, "Bob Martin")
        self.assertEqual(results[0].url, f"/people?person={self.person.uuid}")

    def test_matches_email_and_reports_it(self):
        results = search_persons("ACME", self.alice, 10)
        self.assertEqual(results[0].matched_value, "bob@acme.com")

    def test_never_leaks_other_users(self):
        self.assertEqual(search_persons("hidden", self.alice, 10), [])

    def test_blank_query(self):
        self.assertEqual(search_persons("   ", self.alice, 10), [])

    def test_provider_and_commands_registered(self):
        hits = registry.search("martin", self.alice, limit=10)
        self.assertEqual([h["provider_slug"] for h in hits], ["people"])
        names = [c.name for c in registry.get_active_commands()]
        self.assertIn("People", names)
        self.assertIn("New contact", names)
