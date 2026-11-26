from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from workspace.users.models import GroupVisibility

User = get_user_model()


class AdminChangeListTests(TestCase):
    """The themed admin change lists must render under the installed Django."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        self.client.force_login(self.admin)

    def test_knox_token_change_list_renders(self):
        response = self.client.get("/admin/knox/authtoken/")
        self.assertEqual(response.status_code, 200)

    def test_user_change_list_renders_rows(self):
        response = self.client.get("/admin/auth/user/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "root@example.com")


class GroupVisibilityAdminTests(TestCase):
    """The hidden flag is set from the group's own admin page."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        self.client.force_login(self.admin)
        self.group = Group.objects.create(name="Council")

    def save(self, **visibility):
        return self.client.post(
            f"/admin/auth/group/{self.group.pk}/change/",
            {
                "name": "Council",
                "visibility-TOTAL_FORMS": "1",
                "visibility-INITIAL_FORMS": "1" if self.group_has_row() else "0",
                "visibility-MIN_NUM_FORMS": "0",
                "visibility-MAX_NUM_FORMS": "1",
                **visibility,
                "storage_quota-TOTAL_FORMS": "0",
                "storage_quota-INITIAL_FORMS": "0",
                "storage_quota-MIN_NUM_FORMS": "0",
                "storage_quota-MAX_NUM_FORMS": "1",
            },
        )

    def group_has_row(self):
        return GroupVisibility.objects.filter(group=self.group).exists()

    def test_saving_a_group_untouched_keeps_it_visible_without_a_row(self):
        response = self.save()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.group_has_row())

    def test_ticking_hidden_hides_the_group(self):
        response = self.save(**{"visibility-0-is_hidden": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(GroupVisibility.objects.get(group=self.group).is_hidden)

    def test_the_change_list_shows_and_filters_hidden_groups(self):
        Group.objects.create(name="Everyone")
        GroupVisibility.objects.create(group=self.group, is_hidden=True)
        response = self.client.get("/admin/auth/group/", {"hidden": "no"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [g.name for g in response.context["cl"].result_list], ["Everyone"]
        )
        response = self.client.get("/admin/auth/group/", {"hidden": "yes"})
        self.assertEqual(
            [g.name for g in response.context["cl"].result_list], ["Council"]
        )
