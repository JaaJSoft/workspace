import uuid

from django.test import SimpleTestCase

from workspace.chat.services import participant_keys as keys


class UserKeyTests(SimpleTestCase):
    def test_user_key_format(self):
        self.assertEqual(keys.user_key(12), "u:12")

    def test_user_key_round_trips(self):
        self.assertEqual(keys.user_id_from_key(keys.user_key(12)), 12)

    def test_is_user_key(self):
        self.assertTrue(keys.is_user_key("u:12"))
        self.assertFalse(keys.is_user_key("g:abc"))
        self.assertFalse(keys.is_user_key(""))
        self.assertFalse(keys.is_user_key(None))
        self.assertFalse(keys.is_user_key(12))


class GuestKeyTests(SimpleTestCase):
    def test_guest_key_format(self):
        guest_uuid = uuid.UUID("0192f7c0-0000-7000-8000-000000000001")
        self.assertEqual(keys.guest_key(guest_uuid), f"g:{guest_uuid}")

    def test_is_guest_key(self):
        self.assertTrue(keys.is_guest_key("g:0192f7c0-0000-7000-8000-000000000001"))
        self.assertFalse(keys.is_guest_key("u:12"))
        self.assertFalse(keys.is_guest_key(None))


class MalformedKeyTests(SimpleTestCase):
    def test_user_id_from_key_rejects_non_user_keys(self):
        self.assertIsNone(keys.user_id_from_key("g:abc"))
        self.assertIsNone(keys.user_id_from_key("12"))
        self.assertIsNone(keys.user_id_from_key(""))
        self.assertIsNone(keys.user_id_from_key(None))

    def test_user_id_from_key_rejects_non_numeric_payload(self):
        self.assertIsNone(keys.user_id_from_key("u:"))
        self.assertIsNone(keys.user_id_from_key("u:abc"))
        self.assertIsNone(keys.user_id_from_key("u:1:2"))

    def test_keys_are_totally_ordered(self):
        # chatCallShouldDriveIceRestart elects a single offerer by comparing two
        # keys. Any total order works; this pins that mixed kinds compare.
        ordered = sorted(["u:2", "g:abc", "u:10"])
        self.assertEqual(len(set(ordered)), 3)
        self.assertEqual(ordered[0], "g:abc")
