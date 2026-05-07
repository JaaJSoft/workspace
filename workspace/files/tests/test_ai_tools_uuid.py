"""Regression: AI tool params reject malformed UUIDs at the boundary.

Before the fix, ``ReadFileParams.uuid`` was typed ``str`` and the tool
hand-rolled an inline ``uuid.UUID(value)`` parse. Switching to a
Pydantic ``uuid.UUID`` field centralizes the validation so a malformed
UUID is rejected before reaching ``filter(uuid=...)``.
"""
from pydantic import ValidationError
from rest_framework.test import APITestCase

from workspace.files.ai_tools import ReadFileParams


class ReadFileParamsValidationTests(APITestCase):

    def test_malformed_uuid_rejected_by_pydantic(self):
        with self.assertRaises(ValidationError):
            ReadFileParams(uuid='not-a-uuid')

    def test_well_formed_uuid_accepted(self):
        params = ReadFileParams(uuid='12345678-1234-5678-1234-567812345678')
        self.assertEqual(str(params.uuid), '12345678-1234-5678-1234-567812345678')
