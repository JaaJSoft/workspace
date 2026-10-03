from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.people.services.persons import create_person

User = get_user_model()

URL = "/mail/contact-card"


class ContactCardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="reader", password="x")
        self.carol = User.objects.create_user(
            username="carol",
            password="x",
            email="carol@corp.com",
            first_name="Carol",
            last_name="Danvers",
        )
        self.client.force_login(self.user)

    def _card(self, **params):
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_requires_login(self):
        self.client.logout()
        response = self.client.get(URL, {"email": "bob@example.com"})
        self.assertEqual(response.status_code, 302)

    def test_requires_an_email(self):
        self.assertEqual(self.client.get(URL).status_code, 400)
        self.assertEqual(self.client.get(URL, {"email": "  "}).status_code, 400)

    def test_unknown_address_offers_to_add_it(self):
        html = self._card(email="bob@example.com", name="Bob Martin")
        self.assertIn("Bob Martin", html)
        self.assertIn('data-mail-card-action="add"', html)
        self.assertNotIn("data-user-id", html)
        self.assertNotIn("Open in People", html)

    def test_known_address_shows_the_person(self):
        person = create_person(
            owner=self.user,
            display_name="Robert Martin",
            organization="Acme",
            title="CTO",
            emails=[
                {"value": "bob@example.com", "type": "work"},
                {"value": "bob@home.org", "type": "home"},
            ],
            phones=[{"value": "+33612345678", "type": "cell"}],
        )
        html = self._card(email="BOB@example.com", name="Bob")
        self.assertIn("Robert Martin", html)
        self.assertIn("Acme", html)
        self.assertIn("bob@home.org", html)
        self.assertIn('href="tel:+33612345678"', html)
        self.assertIn(f"/people?person={person.uuid}", html)
        self.assertNotIn('data-mail-card-action="add"', html)

    def test_group_person_is_shown(self):
        team = Group.objects.create(name="team")
        self.user.groups.add(team)
        create_person(
            group=team,
            display_name="Team Bob",
            emails=[{"value": "bob@example.com", "type": "work"}],
        )
        self.assertIn("Team Bob", self._card(email="bob@example.com"))

    def test_someone_elses_person_is_not_shown(self):
        create_person(
            owner=self.carol,
            display_name="Secret Bob",
            emails=[{"value": "bob@example.com", "type": "work"}],
        )
        html = self._card(email="bob@example.com")
        self.assertNotIn("Secret Bob", html)
        self.assertIn('data-mail-card-action="add"', html)

    def test_workspace_account_is_named_and_promoted_by_id(self):
        html = self._card(email="Carol@Corp.com")
        self.assertIn("Carol Danvers", html)
        self.assertIn("@carol", html)
        self.assertIn(f'data-user-id="{self.carol.pk}"', html)
        self.assertIn('data-mail-card-action="add"', html)

    def test_person_linked_to_the_account_wins(self):
        create_person(
            owner=self.user,
            display_name="Captain",
            emails=[{"value": "carol@corp.com", "type": "work"}],
            linked_user=self.carol,
        )
        html = self._card(email="carol@corp.com")
        self.assertIn("Captain", html)
        self.assertNotIn("data-user-id", html)
        self.assertNotIn('data-mail-card-action="add"', html)

    def test_values_are_escaped(self):
        html = self._card(email="x@example.com", name="<script>alert(1)</script>")
        self.assertNotIn("<script>alert(1)</script>", html)
