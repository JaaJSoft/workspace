from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

User = get_user_model()


class PathSegmentUsernameTests(TestCase):
    """A username is a storage path segment: "." and ".." are refused."""

    def test_a_user_cannot_be_created_under_a_path_segment(self):
        for name in (".", ".."):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                User.objects.create_user(name)
        self.assertFalse(User.objects.filter(username__in=(".", "..")).exists())

    def test_a_user_cannot_be_renamed_to_a_path_segment(self):
        user = User.objects.create_user("alice")
        user.username = ".."
        with self.assertRaises(ValidationError):
            user.save()
        self.assertTrue(User.objects.filter(pk=user.pk, username="alice").exists())

    def test_an_account_named_so_before_the_rule_keeps_saving(self):
        user = User.objects.create_user("legacy")
        User.objects.filter(pk=user.pk).update(username="..")
        user.refresh_from_db()

        user.first_name = "Old"
        user.save()
        user.save(update_fields=["last_login"])

        self.assertEqual(User.objects.get(pk=user.pk).first_name, "Old")

    def test_a_name_made_of_more_dots_is_an_ordinary_name(self):
        User.objects.create_user("...")
        self.assertTrue(User.objects.filter(username="...").exists())


class AdminUsernameTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw"
        )
        self.client.force_login(self.admin)

    def test_the_add_form_refuses_a_path_segment(self):
        response = self.client.post(
            "/admin/auth/user/add/",
            {"username": "..", "usable_password": "false"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "cannot be used as a username")
        self.assertFalse(User.objects.filter(username="..").exists())

    def test_the_change_form_refuses_a_rename_to_a_path_segment(self):
        user = User.objects.create_user("bob")
        form_class = admin.site.get_model_admin(User).form

        form = form_class(
            instance=user, data={"username": "..", "date_joined": user.date_joined}
        )

        self.assertFalse(form.is_valid())
        self.assertIn("username", form.errors)

    def test_the_change_form_keeps_an_account_named_so_before_the_rule(self):
        user = User.objects.create_user("legacy")
        User.objects.filter(pk=user.pk).update(username=".")
        user.refresh_from_db()
        form_class = admin.site.get_model_admin(User).form

        form = form_class(
            instance=user,
            data={
                "username": ".",
                "first_name": "Old",
                "date_joined": user.date_joined,
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
