from django.contrib.auth import get_user_model
from django.test import TestCase

from workspace.users.serializers import UserSummarySerializer

User = get_user_model()


class UserSummarySerializerTests(TestCase):
    def test_holds_what_drawing_a_user_takes_and_nothing_else(self):
        user = User.objects.create_user(
            username="alice",
            email="alice@example.com",
            first_name="Alice",
            last_name="Martin",
        )
        self.assertEqual(
            UserSummarySerializer(user).data,
            {
                "id": user.pk,
                "username": "alice",
                "first_name": "Alice",
                "last_name": "Martin",
                "avatar_url": f"/api/v1/users/{user.pk}/avatar",
            },
        )
