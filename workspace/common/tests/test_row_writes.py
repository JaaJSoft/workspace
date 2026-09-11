from django.contrib.auth import get_user_model
from django.db.models.signals import pre_save
from django.test import TestCase

from workspace.common.tests.row_writes import (
    FullRowWrite,
    guard_full_row_writes,
    written_by_test_code,
)

User = get_user_model()


class WrittenByTestCodeTests(TestCase):
    def test_a_tests_package_is_test_code(self):
        self.assertTrue(written_by_test_code("workspace/files/tests/test_api.py"))
        self.assertTrue(written_by_test_code("workspace/common/tests/media.py"))

    def test_application_code_is_not(self):
        self.assertFalse(written_by_test_code("workspace/files/services/files.py"))
        # A module whose name merely contains "test" is not a tests package.
        self.assertFalse(written_by_test_code("workspace/core/latest.py"))


class GuardFullRowWritesTests(TestCase):
    """The guard itself, driven with the test exemption turned off.

    Every caller here is test code, which the guard normally lets through, so
    these pass ``exempt`` to make the assertions reachable at all.
    """

    def setUp(self):
        self.receivers = []

    def tearDown(self):
        for receiver in self.receivers:
            pre_save.disconnect(receiver, sender=User)

    def _arm(self, **kwargs):
        kwargs.setdefault("exempt", lambda path: False)
        self.receivers.append(guard_full_row_writes(User, **kwargs))

    def _user(self):
        return User.objects.create_user(
            username="guarded", email="g@test.com", password="pw"
        )

    def test_a_full_row_save_is_refused(self):
        user = self._user()
        self._arm()
        user.first_name = "Ada"
        with self.assertRaises(FullRowWrite) as caught:
            user.save()
        self.assertIn("update_fields", str(caught.exception))
        self.assertIn("test_row_writes.py", str(caught.exception))

    def test_a_scoped_save_goes_through(self):
        user = self._user()
        self._arm()
        user.first_name = "Ada"
        user.save(update_fields=["first_name"])
        user.refresh_from_db()
        self.assertEqual(user.first_name, "Ada")

    def test_an_insert_goes_through(self):
        """A new row has no snapshot to republish."""
        self._arm()
        self.assertIsNotNone(self._user().pk)

    def test_test_code_is_exempt_by_default(self):
        user = self._user()
        self.receivers.append(guard_full_row_writes(User))
        user.first_name = "Ada"
        user.save()  # this file is a tests package, so the guard stands down

    def test_a_forwarded_frame_is_never_the_culprit(self):
        """Machinery that passes a save along must not take the blame for it."""
        self._arm(
            forwarded_by=(
                "workspace/common/tests/test_row_writes.py:_save_through_a_helper",
            )
        )
        user = self._user()
        user.first_name = "Ada"
        with self.assertRaises(FullRowWrite) as caught:
            self._save_through_a_helper(user)
        # The helper is stepped over, so the line named is this method's call.
        self.assertNotIn("_save_through_a_helper", str(caught.exception))

    def _save_through_a_helper(self, user):
        user.save()
