from django import forms
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.http import QueryDict
from django.test import TestCase
from django.utils import timezone
from django_filters import rest_framework as filters

from workspace.common.filters import BooleanFilter, MultiValueFilter, TextSearchFilter

User = get_user_model()


class _UserFilter(filters.FilterSet):
    active = BooleanFilter(field_name="is_active")
    logged_in = BooleanFilter(
        field_name="last_login", lookup_expr="isnull", exclude=True
    )

    class Meta:
        model = User
        fields = []


class BooleanFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User.objects.create_user(username="active", last_login=timezone.now())
        User.objects.create_user(username="inactive", is_active=False)

    def _names(self, query):
        filterset = _UserFilter(QueryDict(query), queryset=User.objects.all())
        self.assertTrue(filterset.is_valid(), filterset.errors)
        return sorted(filterset.qs.values_list("username", flat=True))

    def test_absent_or_empty_is_no_filter(self):
        self.assertEqual(self._names(""), ["active", "inactive"])
        self.assertEqual(self._names("active="), ["active", "inactive"])

    def test_true_and_false_each_keep_their_half(self):
        self.assertEqual(self._names("active=true"), ["active"])
        self.assertEqual(self._names("active=false"), ["inactive"])
        self.assertEqual(self._names("active=1"), ["active"])
        self.assertEqual(self._names("active=0"), ["inactive"])

    def test_a_nullable_column_reads_as_a_state(self):
        self.assertEqual(self._names("logged_in=true"), ["active"])
        self.assertEqual(self._names("logged_in=false"), ["inactive"])

    def test_an_unreadable_value_is_refused(self):
        filterset = _UserFilter(
            QueryDict("active=perhaps"), queryset=User.objects.all()
        )
        self.assertFalse(filterset.is_valid())
        self.assertEqual(list(filterset.errors), ["active"])


class _PermissionFilter(filters.FilterSet):
    content_type = MultiValueFilter(child=forms.IntegerField())
    codename = MultiValueFilter()

    class Meta:
        model = Permission
        fields = []


class _LogEntryFilter(filters.FilterSet):
    content_type = MultiValueFilter(child=forms.IntegerField(), allow_none=True)

    class Meta:
        model = LogEntry
        fields = []


class _GroupFilter(filters.FilterSet):
    permission = MultiValueFilter(
        field_name="permissions", child=forms.IntegerField(), distinct=True
    )

    class Meta:
        model = Group
        fields = []


class MultiValueFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.group_type = ContentType.objects.get_for_model(Group)
        cls.permission_type = ContentType.objects.get_for_model(Permission)

    def _codenames(self, query):
        filterset = _PermissionFilter(
            QueryDict(query), queryset=Permission.objects.all()
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        return sorted(filterset.qs.values_list("codename", flat=True))

    def test_absent_is_no_filter(self):
        self.assertEqual(len(self._codenames("")), Permission.objects.count())

    def test_one_value(self):
        self.assertEqual(self._codenames("codename=add_group"), ["add_group"])

    def test_repeated_and_comma_separated_values_are_the_same_question(self):
        expected = ["add_group", "view_group"]
        self.assertEqual(
            self._codenames("codename=add_group&codename=view_group"), expected
        )
        self.assertEqual(self._codenames("codename=add_group,view_group"), expected)
        self.assertEqual(
            self._codenames("codename=add_group,%20view_group&codename="), expected
        )

    def test_values_of_one_filter_are_or_and_filters_are_and(self):
        both_types = f"content_type={self.group_type.pk},{self.permission_type.pk}"
        self.assertEqual(len(self._codenames(both_types)), 8)
        self.assertEqual(
            self._codenames(f"{both_types}&codename=add_group,add_permission,add_user"),
            ["add_group", "add_permission"],
        )

    def test_a_value_the_child_refuses_is_refused(self):
        filterset = _PermissionFilter(
            QueryDict("content_type=1,abc"), queryset=Permission.objects.all()
        )
        self.assertFalse(filterset.is_valid())
        self.assertEqual(list(filterset.errors), ["content_type"])

    def test_none_asks_for_the_rows_holding_no_value(self):
        user = User.objects.create_user(username="actor")
        common = {"user": user, "action_flag": 1, "object_repr": "x"}
        typed = LogEntry.objects.create(content_type=self.group_type, **common)
        untyped = LogEntry.objects.create(content_type=None, **common)

        def pks(query):
            filterset = _LogEntryFilter(
                QueryDict(query), queryset=LogEntry.objects.all()
            )
            self.assertTrue(filterset.is_valid(), filterset.errors)
            return sorted(filterset.qs.values_list("pk", flat=True))

        self.assertEqual(pks("content_type=none"), [untyped.pk])
        self.assertEqual(
            pks(f"content_type=none,{self.group_type.pk}"),
            sorted([typed.pk, untyped.pk]),
        )

    def test_none_is_an_ordinary_value_where_it_is_not_allowed(self):
        filterset = _PermissionFilter(
            QueryDict("content_type=none"), queryset=Permission.objects.all()
        )
        self.assertFalse(filterset.is_valid())
        self.assertEqual(self._codenames("codename=none"), [])

    def test_distinct_returns_a_row_matching_two_values_once(self):
        group = Group.objects.create(name="editors")
        permissions = list(Permission.objects.filter(content_type=self.group_type)[:2])
        group.permissions.set(permissions)
        query = "&".join(f"permission={permission.pk}" for permission in permissions)
        filterset = _GroupFilter(QueryDict(query), queryset=Group.objects.all())
        self.assertEqual(list(filterset.qs), [group])


class _GroupSearch(filters.FilterSet):
    q = TextSearchFilter(fields=("name", "permissions__codename"))

    class Meta:
        model = Group
        fields = []


class TextSearchFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Group.objects.create(name="Équipe Support")
        managers = Group.objects.create(name="Managers")
        managers.permissions.add(Permission.objects.get(codename="add_group"))

    def _names(self, query):
        filterset = _GroupSearch(QueryDict(query), queryset=Group.objects.all())
        return sorted(filterset.qs.values_list("name", flat=True))

    def test_absent_or_blank_is_no_filter(self):
        self.assertEqual(self._names(""), ["Managers", "Équipe Support"])
        self.assertEqual(self._names("q=%20%20"), ["Managers", "Équipe Support"])

    def test_matches_ignoring_case_and_accents(self):
        self.assertEqual(self._names("q=equipe"), ["Équipe Support"])
        self.assertEqual(self._names("q=SUPPORT"), ["Équipe Support"])

    def test_any_of_the_fields_matches(self):
        self.assertEqual(self._names("q=add_group"), ["Managers"])
