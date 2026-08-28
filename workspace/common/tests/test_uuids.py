import uuid
from unittest import mock

from django.test import SimpleTestCase

from workspace.common.uuids import (
    BatchTooLarge,
    MalformedBatch,
    MalformedUuid,
    UuidBatchError,
    parse_uuid_batch,
    parse_uuid_or_none,
    uuid_v7_or_v4,
)


class UuidV7OrV4Tests(SimpleTestCase):
    def test_returns_uuid_instance(self):
        value = uuid_v7_or_v4()
        self.assertIsInstance(value, uuid.UUID)

    def test_returns_unique_values(self):
        values = {uuid_v7_or_v4() for _ in range(100)}
        self.assertEqual(len(values), 100)

    def test_prefers_uuid7_when_available(self):
        sentinel = uuid.UUID("018f8a0f-7b5d-7a1e-9c4b-0123456789ab")
        fake_uuid7 = mock.Mock(return_value=sentinel)

        with mock.patch("workspace.common.uuids.uuid") as mocked_uuid:
            mocked_uuid.uuid7 = fake_uuid7
            mocked_uuid.uuid4 = mock.Mock(
                side_effect=AssertionError("uuid4 must not be called")
            )
            result = uuid_v7_or_v4()

        fake_uuid7.assert_called_once()
        self.assertEqual(result, sentinel)

    def test_falls_back_to_uuid4_when_uuid7_missing(self):
        fallback = uuid.UUID("12345678-1234-4234-8234-123456789abc")

        with mock.patch("workspace.common.uuids.uuid") as mocked_uuid:
            # Simulate an older stdlib: no uuid7 attribute at all.
            del mocked_uuid.uuid7
            mocked_uuid.uuid4 = mock.Mock(return_value=fallback)
            result = uuid_v7_or_v4()

        mocked_uuid.uuid4.assert_called_once()
        self.assertEqual(result, fallback)

    def test_falls_back_when_uuid7_is_not_callable(self):
        with mock.patch("workspace.common.uuids.uuid") as mocked_uuid:
            mocked_uuid.uuid7 = "not-callable"
            mocked_uuid.uuid4 = mock.Mock(return_value=uuid.UUID(int=0))
            result = uuid_v7_or_v4()

        mocked_uuid.uuid4.assert_called_once()
        self.assertEqual(result, uuid.UUID(int=0))

    def test_falls_back_when_uuid7_raises(self):
        failing = mock.Mock(side_effect=RuntimeError("boom"))
        fallback = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")

        with mock.patch("workspace.common.uuids.uuid") as mocked_uuid:
            mocked_uuid.uuid7 = failing
            mocked_uuid.uuid4 = mock.Mock(return_value=fallback)
            result = uuid_v7_or_v4()

        failing.assert_called_once()
        mocked_uuid.uuid4.assert_called_once()
        self.assertEqual(result, fallback)


class ParseUuidOrNoneTests(SimpleTestCase):
    def test_parses_valid_string(self):
        value = "018f8a0f-7b5d-7a1e-9c4b-0123456789ab"
        self.assertEqual(parse_uuid_or_none(value), uuid.UUID(value))

    def test_accepts_uuid_instance(self):
        value = uuid.uuid4()
        self.assertEqual(parse_uuid_or_none(value), value)

    def test_returns_none_for_malformed_string(self):
        self.assertIsNone(parse_uuid_or_none("not-a-uuid"))

    def test_returns_none_for_none(self):
        self.assertIsNone(parse_uuid_or_none(None))

    def test_returns_none_for_non_uuid_number(self):
        self.assertIsNone(parse_uuid_or_none(123))


class ParseUuidBatchTests(SimpleTestCase):
    def setUp(self):
        self.one = str(uuid.uuid4())

    def test_parses_a_well_formed_batch(self):
        self.assertEqual(parse_uuid_batch({"uuids": [self.one]}), [uuid.UUID(self.one)])

    def test_a_body_that_is_not_an_object_is_refused(self):
        """The guard the callers exist for: a JSON array or scalar reaches a
        view as a list or an int, and reading a key off it raises
        AttributeError - a 500 where the endpoint promises a 400."""
        for body in ([self.one], 42, "x", None):
            with self.subTest(body=body):
                with self.assertRaises(MalformedBatch):
                    parse_uuid_batch(body)

    def test_a_missing_or_empty_list_is_refused(self):
        for body in ({}, {"uuids": []}, {"uuids": "nope"}):
            with self.subTest(body=body):
                with self.assertRaises(MalformedBatch):
                    parse_uuid_batch(body)

    def test_a_malformed_uuid_is_refused(self):
        with self.assertRaises(MalformedUuid):
            parse_uuid_batch({"uuids": [self.one, "not-a-uuid"]})

    def test_a_batch_above_the_cap_is_refused_not_truncated(self):
        with self.assertRaises(BatchTooLarge):
            parse_uuid_batch({"uuids": [self.one] * 4}, max_items=3)

    def test_every_kind_is_catchable_as_the_base_error(self):
        """Views distinguish the kinds; a caller that does not care catches
        the base and still gets every refusal."""
        for kind in (MalformedBatch, BatchTooLarge, MalformedUuid):
            with self.subTest(kind=kind):
                self.assertTrue(issubclass(kind, UuidBatchError))

    def test_duplicates_are_kept_so_the_caller_can_map_them_back(self):
        """Deduplicating here would lose the position of each submitted
        spelling, which the vault endpoint keys its answer by."""
        self.assertEqual(len(parse_uuid_batch({"uuids": [self.one] * 3})), 3)

    def test_the_key_is_configurable(self):
        self.assertEqual(
            parse_uuid_batch({"ids": [self.one]}, key="ids"), [uuid.UUID(self.one)]
        )
