"""WOPI access tokens: roundtrip, scoping, expiry, tampering."""

import uuid as uuid_module

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from workspace.files.services.wopi.tokens import (
    mint_access_token,
    parse_access_token,
)

User = get_user_model()


class WopiTokenTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="wopi_user", email="wopi@test.com", password="pw"
        )
        self.file_uuid = uuid_module.uuid4()

    def test_roundtrip_preserves_user_and_write_flag(self):
        token = mint_access_token(self.user, self.file_uuid, can_write=True)
        parsed = parse_access_token(token, self.file_uuid)
        self.assertIsNotNone(parsed)
        user, can_write = parsed
        self.assertEqual(user.pk, self.user.pk)
        self.assertTrue(can_write)

    def test_read_only_token_reports_no_write(self):
        token = mint_access_token(self.user, self.file_uuid, can_write=False)
        _user, can_write = parse_access_token(token, self.file_uuid)
        self.assertFalse(can_write)

    def test_token_is_scoped_to_its_file(self):
        token = mint_access_token(self.user, self.file_uuid, can_write=True)
        self.assertIsNone(parse_access_token(token, uuid_module.uuid4()))

    def test_tampered_token_is_rejected(self):
        token = mint_access_token(self.user, self.file_uuid, can_write=True)
        self.assertIsNone(parse_access_token(token + "x", self.file_uuid))
        self.assertIsNone(parse_access_token("", self.file_uuid))

    @override_settings(WOPI_TOKEN_TTL=-1)
    def test_expired_token_is_rejected(self):
        token = mint_access_token(self.user, self.file_uuid, can_write=True)
        self.assertIsNone(parse_access_token(token, self.file_uuid))

    def test_inactive_user_is_rejected(self):
        token = mint_access_token(self.user, self.file_uuid, can_write=True)
        self.user.is_active = False
        self.user.save()
        self.assertIsNone(parse_access_token(token, self.file_uuid))
