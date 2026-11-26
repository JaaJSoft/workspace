"""The API follows its standard, except where it is known not to yet.

The standard (CLAUDE.md, *API standard*) was written after most endpoints, so
the ones that predate it are listed in ``api_standard_gaps.KNOWN_GAPS`` and
leave the list as their module is brought in line. This test holds both ends:
a gap outside the list is a new endpoint written against the standard, and an
entry that is no longer a gap is one to delete, so the list cannot grow back.
"""

from django.test import TestCase

from .api_standard import find_gaps
from .api_standard_gaps import KNOWN_GAPS

ADVICE = {
    "namespace": (
        "the first segment after /api/v1 is the module, or one of the root "
        "resources the standard names"
    ),
    "path parameter": (
        "the resource a path addresses is {uuid}, a parent before it is "
        "{<resource>_uuid}"
    ),
    "nesting": (
        "one parent at most: a resource with its own uuid is read, updated and "
        "deleted without its ancestors in the path"
    ),
    "path vocabulary": (
        "no verb for what the HTTP method says; a lot is /batch, the available "
        "actions are /available-actions, a search is the q filter of the list"
    ),
    "query parameter": (
        "q for a search, limit and offset for a page, the bare name for a "
        "relation, <field>_after and <field>_before for a range, include_x "
        "rather than show_x"
    ),
    "update without PATCH": "an updatable resource takes PATCH; PUT only next to it",
    "schema complaint": (
        "declare a serializer for the request and the response; the OpenAPI "
        "schema generates without a warning"
    ),
    "query parameter read by hand": (
        "a filter is declared in the module's FilterSet, any other query "
        "parameter is read through a serializer"
    ),
    "error without a body": (
        "raise NotFound, PermissionDenied or ValidationError: every error has "
        'a {"detail": ...} body'
    ),
    "error under another key": 'an error is {"detail": ...}, never {"error": ...}',
    "another paginator": (
        "a list answers through ListPagination of workspace/common/pagination.py"
    ),
}


class ApiStandardTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gaps = find_gaps()

    def test_every_rule_checked_has_its_advice(self):
        self.assertEqual(set(self.gaps) | set(KNOWN_GAPS), set(ADVICE))

    def test_no_gap_outside_the_known_ones(self):
        for rule, advice in ADVICE.items():
            with self.subTest(rule=rule):
                new = sorted(self.gaps.get(rule, set()) - KNOWN_GAPS.get(rule, set()))
                self.assertFalse(
                    new,
                    f"Against the API standard ({rule}): {advice}. Fix these "
                    f"rather than listing them as known gaps: {new}",
                )

    def test_no_known_gap_that_is_one_no_longer(self):
        for rule in ADVICE:
            with self.subTest(rule=rule):
                closed = sorted(
                    KNOWN_GAPS.get(rule, set()) - self.gaps.get(rule, set())
                )
                self.assertFalse(
                    closed,
                    f"No longer gaps ({rule}): remove them from "
                    f"api_standard_gaps.KNOWN_GAPS: {closed}",
                )
