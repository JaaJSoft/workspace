"""The groups the new-conversation dialog offers.

A group conversation takes its members from its groups, so any group the
creator may name can be linked to one, as long as one of them is the
creator's. A hidden group is offered to its members only.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.users.models import GroupVisibility

User = get_user_model()


class DialogGroupsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ada", password="pass")
        self.sales = Group.objects.create(name="Sales")
        self.design = Group.objects.create(name="design")
        self.accounting = Group.objects.create(name="Accounting")
        self.user.groups.add(self.design)
        self.client.force_login(self.user)

    def test_the_page_offers_every_group_the_users_own_first(self):
        response = self.client.get("/chat")

        self.assertEqual(
            response.context["chat_groups"],
            [
                {"id": self.design.pk, "name": "design", "mine": True},
                {"id": self.accounting.pk, "name": "Accounting", "mine": False},
                {"id": self.sales.pk, "name": "Sales", "mine": False},
            ],
        )

    def test_a_user_in_no_group_still_sees_the_groups(self):
        self.user.groups.clear()

        groups = self.client.get("/chat").context["chat_groups"]

        self.assertEqual(
            [group["name"] for group in groups], ["Accounting", "design", "Sales"]
        )
        self.assertFalse(any(group["mine"] for group in groups))

    def test_a_hidden_group_is_offered_to_its_members_only(self):
        GroupVisibility.objects.create(group=self.sales, is_hidden=True)
        GroupVisibility.objects.create(group=self.design, is_hidden=True)

        groups = self.client.get("/chat").context["chat_groups"]

        self.assertEqual([group["name"] for group in groups], ["design", "Accounting"])
