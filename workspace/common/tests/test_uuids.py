import uuid
from unittest import mock

from django.test import SimpleTestCase

from workspace.common.uuids import uuid_v7_or_v4


class UuidV7OrV4Tests(SimpleTestCase):
    def test_returns_uuid_instance(self):
        value = uuid_v7_or_v4()
        self.assertIsInstance(value, uuid.UUID)

    def test_returns_unique_values(self):
        values = {uuid_v7_or_v4() for _ in range(100)}
        self.assertEqual(len(values), 100)

    def test_prefers_uuid7_when_available(self):
        sentinel = uuid.UUID('018f8a0f-7b5d-7a1e-9c4b-0123456789ab')
        fake_uuid7 = mock.Mock(return_value=sentinel)

        with mock.patch('workspace.common.uuids.uuid') as mocked_uuid:
            mocked_uuid.uuid7 = fake_uuid7
            mocked_uuid.uuid4 = mock.Mock(side_effect=AssertionError('uuid4 must not be called'))
            result = uuid_v7_or_v4()

        fake_uuid7.assert_called_once()
        self.assertEqual(result, sentinel)

    def test_falls_back_to_uuid4_when_uuid7_missing(self):
        fallback = uuid.UUID('12345678-1234-4234-8234-123456789abc')

        with mock.patch('workspace.common.uuids.uuid') as mocked_uuid:
            # Simulate an older stdlib: no uuid7 attribute at all.
            del mocked_uuid.uuid7
            mocked_uuid.uuid4 = mock.Mock(return_value=fallback)
            result = uuid_v7_or_v4()

        mocked_uuid.uuid4.assert_called_once()
        self.assertEqual(result, fallback)

    def test_falls_back_when_uuid7_is_not_callable(self):
        with mock.patch('workspace.common.uuids.uuid') as mocked_uuid:
            mocked_uuid.uuid7 = 'not-callable'
            mocked_uuid.uuid4 = mock.Mock(return_value=uuid.UUID(int=0))
            result = uuid_v7_or_v4()

        mocked_uuid.uuid4.assert_called_once()
        self.assertEqual(result, uuid.UUID(int=0))

    def test_falls_back_when_uuid7_raises(self):
        failing = mock.Mock(side_effect=RuntimeError('boom'))
        fallback = uuid.UUID('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee')

        with mock.patch('workspace.common.uuids.uuid') as mocked_uuid:
            mocked_uuid.uuid7 = failing
            mocked_uuid.uuid4 = mock.Mock(return_value=fallback)
            result = uuid_v7_or_v4()

        failing.assert_called_once()
        mocked_uuid.uuid4.assert_called_once()
        self.assertEqual(result, fallback)
