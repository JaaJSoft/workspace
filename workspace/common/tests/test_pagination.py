from django.test import SimpleTestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from workspace.common.pagination import OptInLimitOffsetPagination


class OptInLimitOffsetPaginationTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.paginator = OptInLimitOffsetPagination()
        self.data = list(range(10))

    def _paginate(self, default_offset=None, **params):
        request = Request(self.factory.get("/", params))
        return self.paginator.paginate_queryset(
            self.data, request, default_offset=default_offset
        )

    def test_no_limit_leaves_the_response_unpaginated(self):
        self.assertIsNone(self._paginate())

    def test_limit_slices_and_reports_more(self):
        page = self._paginate(limit="3")
        self.assertEqual(page, [0, 1, 2])
        self.assertTrue(self.paginator.has_more)

    def test_offset_shifts_the_window(self):
        page = self._paginate(limit="3", offset="8")
        self.assertEqual(page, [8, 9])
        self.assertFalse(self.paginator.has_more)

    def test_exact_boundary_reports_no_more(self):
        page = self._paginate(limit="5", offset="5")
        self.assertEqual(page, [5, 6, 7, 8, 9])
        self.assertFalse(self.paginator.has_more)

    def test_offset_past_the_end_yields_empty_page(self):
        page = self._paginate(limit="3", offset="50")
        self.assertEqual(page, [])
        self.assertFalse(self.paginator.has_more)

    def test_invalid_and_zero_limits_fall_back_to_unpaginated(self):
        self.assertIsNone(self._paginate(limit="abc"))
        self.assertIsNone(self._paginate(limit="0"))

    def test_limit_is_clamped_to_max_limit(self):
        self._paginate(limit="99999")
        self.assertEqual(self.paginator.limit, 1000)

    def test_paginated_response_is_a_bare_array_with_header(self):
        page = self._paginate(limit="4")
        response = self.paginator.get_paginated_response(page)
        self.assertEqual(response.data, [0, 1, 2, 3])
        self.assertEqual(response["X-Has-More"], "true")

    def test_header_is_false_on_the_last_page(self):
        page = self._paginate(limit="4", offset="8")
        response = self.paginator.get_paginated_response(page)
        self.assertEqual(response.data, [8, 9])
        self.assertEqual(response["X-Has-More"], "false")

    def test_default_offset_places_a_request_that_names_none(self):
        page = self._paginate(limit="3", default_offset=lambda limit: 10 - limit)
        self.assertEqual(page, [7, 8, 9])
        self.assertEqual(self.paginator.offset, 7)
        self.assertFalse(self.paginator.has_more)

    def test_an_offset_in_the_request_wins_over_the_default(self):
        def default_offset(limit):
            raise AssertionError("not called when the request names an offset")

        page = self._paginate(limit="3", offset="2", default_offset=default_offset)
        self.assertEqual(page, [2, 3, 4])

    def test_default_offset_is_not_called_without_a_limit(self):
        def default_offset(limit):
            raise AssertionError("not called on an unpaginated request")

        self.assertIsNone(self._paginate(default_offset=default_offset))

    def test_schema_stays_an_array(self):
        schema = {"type": "array", "items": {"type": "object"}}
        self.assertEqual(self.paginator.get_paginated_response_schema(schema), schema)
