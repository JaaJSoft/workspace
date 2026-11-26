from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from workspace.common.pagination import ListPagination


class ListPaginationTests(TestCase):
    def setUp(self):
        self.paginator = ListPagination()
        self.rows = ContentType.objects.order_by("pk")
        self.pks = list(self.rows.values_list("pk", flat=True))

    def _page(self, **params):
        request = Request(APIRequestFactory().get("/", params))
        return [row.pk for row in self.paginator.paginate_queryset(self.rows, request)]

    def test_no_limit_answers_the_default_page(self):
        self.paginator.default_limit = 3
        self.assertEqual(self._page(), self.pks[:3])
        self.assertTrue(self.paginator.has_more)

    def test_limit_and_offset_place_the_page(self):
        self.assertEqual(self._page(limit="2", offset="3"), self.pks[3:5])

    def test_the_last_page_reports_no_more(self):
        self.assertEqual(self._page(limit="1000"), self.pks)
        self.assertFalse(self.paginator.has_more)

    def test_a_page_ending_on_the_last_row_reports_no_more(self):
        self.assertEqual(
            self._page(limit="2", offset=str(len(self.pks) - 2)), self.pks[-2:]
        )
        self.assertFalse(self.paginator.has_more)

    def test_an_offset_past_the_end_is_an_empty_page(self):
        self.assertEqual(self._page(offset=str(len(self.pks) + 5)), [])
        self.assertFalse(self.paginator.has_more)

    def test_invalid_values_name_their_parameter(self):
        for params in (
            {"limit": "abc"},
            {"limit": "0"},
            {"limit": "1001"},
            {"offset": "-1"},
            {"offset": "x"},
            {"count": "perhaps"},
        ):
            with self.subTest(params=params), self.assertRaises(ValidationError) as ctx:
                self._page(**params)
            self.assertEqual(list(ctx.exception.detail), list(params))

    def test_an_empty_value_is_an_absent_one(self):
        self.paginator.default_limit = 2
        self.assertEqual(self._page(limit="", offset=""), self.pks[:2])

    def test_the_response_is_a_bare_array_with_its_header(self):
        page = self._page(limit="2")
        response = self.paginator.get_paginated_response(page)
        self.assertEqual(response.data, page)
        self.assertEqual(response["X-Has-More"], "true")
        self.assertNotIn("X-Total-Count", response)

    def test_the_total_is_counted_on_demand_only(self):
        with self.assertNumQueries(1):
            self._page(limit="2")
        with self.assertNumQueries(2):
            page = self._page(limit="2", offset="4", count="true")
        response = self.paginator.get_paginated_response(page)
        self.assertEqual(response["X-Total-Count"], str(len(self.pks)))
