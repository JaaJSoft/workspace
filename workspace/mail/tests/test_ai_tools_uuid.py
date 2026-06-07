"""Regression: AI tool params reject malformed UUIDs at the boundary.

Before the fix, ``ReadEmailParams.uuid`` was typed ``str``, so a
malformed UUID flowed through to ``filter(uuid=...)`` where Django's
``UUIDField.to_python`` raised ``ValidationError``. The bot wrapper
caught that, but the symptom was a generic crash rather than a clean
"invalid UUID" error from Pydantic.

Typing the field as ``uuid.UUID`` lets Pydantic reject upfront with a
diagnostic message the model can recover from.
"""

from pydantic import ValidationError
from rest_framework.test import APITestCase

from workspace.mail.ai_tools import ReadEmailParams


class ReadEmailParamsValidationTests(APITestCase):
    def test_malformed_uuid_rejected_by_pydantic(self):
        with self.assertRaises(ValidationError):
            ReadEmailParams(uuid="not-a-uuid")

    def test_well_formed_uuid_accepted(self):
        params = ReadEmailParams(uuid="12345678-1234-5678-1234-567812345678")
        self.assertEqual(str(params.uuid), "12345678-1234-5678-1234-567812345678")
