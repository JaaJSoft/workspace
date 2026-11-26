from django import forms
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.http import QueryDict
from django.test import TestCase
from django.utils import timezone
from django_filters import rest_framework as filters

from workspace.common.filters import (
    BooleanFilter,
    KeyFilter,
    MultiValueFilter,
    OrderingFilter,
    TextSearchFilter,
)

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

    def test_the_filter_draws_as_a_text_input_holding_its_values(self):
        """Regression: the browsable API draws the filter form, and a widget
        without a template raised there."""
        filterset = _PermissionFilter(
            QueryDict("codename=add_group&codename=view_group"),
            queryset=Permission.objects.all(),
        )
        html = str(filterset.form["codename"])
        self.assertIn('type="text"', html)
        self.assertIn('value="add_group,view_group"', html)
        self.assertNotIn(
            "value=", str(_PermissionFilter(QueryDict("")).form["codename"])
        )


class _GroupSearch(filters.FilterSet):
    q = TextSearchFilter(fields=("name", "permissions__codename"))
    without = TextSearchFilter(fields=("name",), exclude=True)
    once = TextSearchFilter(fields=("permissions__codename",), distinct=True)

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

    def test_exclude_keeps_the_rows_that_do_not_match(self):
        self.assertEqual(self._names("without=managers"), ["Équipe Support"])

    def test_distinct_returns_a_row_matching_through_two_related_rows_once(self):
        managers = Group.objects.get(name="Managers")
        managers.permissions.add(Permission.objects.get(codename="change_group"))
        self.assertEqual(self._names("once=_group"), ["Managers"])


class _LogEntryByUser(filters.FilterSet):
    user = MultiValueFilter(child=forms.IntegerField(), allow_me=True, allow_none=True)
    user_username = KeyFilter(field_name="user__username")
    type_model = KeyFilter(field_name="content_type__model")

    class Meta:
        model = LogEntry
        fields = []


class MeAndKeyFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.alice = User.objects.create_user(username="Alice")
        cls.zoe = User.objects.create_user(username="Zoé")
        group_type = ContentType.objects.get_for_model(Group)
        for user, text, kind in (
            (cls.alice, "by alice", group_type),
            (cls.zoe, "by zoe", None),
        ):
            LogEntry.objects.create(
                user=user, action_flag=1, object_repr=text, content_type=kind
            )

    def _entries(self, query, user=None):
        request = type("Request", (), {"user": user})() if user else None
        filterset = _LogEntryByUser(
            QueryDict(query), queryset=LogEntry.objects.all(), request=request
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        return sorted(filterset.qs.values_list("object_repr", flat=True))

    def test_me_names_the_caller(self):
        self.assertEqual(self._entries("user=me", self.alice), ["by alice"])
        self.assertEqual(self._entries("user=me", self.zoe), ["by zoe"])

    def test_me_sits_next_to_other_values(self):
        both = f"user=me,{self.zoe.pk}"
        self.assertEqual(self._entries(both, self.alice), ["by alice", "by zoe"])

    def test_me_names_nobody_without_a_caller(self):
        self.assertEqual(self._entries("user=me"), [])

    def test_me_is_refused_where_it_is_not_allowed(self):
        filterset = _PermissionFilter(
            QueryDict("content_type=me"), queryset=Permission.objects.all()
        )
        self.assertFalse(filterset.is_valid())

    def test_a_key_matches_whole_ignoring_case_and_accents(self):
        self.assertEqual(self._entries("user_username=alice"), ["by alice"])
        self.assertEqual(self._entries("user_username=ZOE"), ["by zoe"])
        self.assertEqual(self._entries("user_username=zo"), [])

    def test_several_keys_are_or(self):
        self.assertEqual(
            self._entries("user_username=alice,zoe"), ["by alice", "by zoe"]
        )
        self.assertEqual(
            self._entries("user_username=alice&user_username=nobody"), ["by alice"]
        )

    def test_a_key_naming_nothing_matches_no_row(self):
        self.assertEqual(self._entries("user_username=nobody"), [])
        self.assertEqual(self._entries("type_model=nothing"), [])

    def test_keys_of_two_relations_are_and(self):
        self.assertEqual(
            self._entries("user_username=alice&type_model=GROUP"), ["by alice"]
        )
        self.assertEqual(self._entries("user_username=zoe&type_model=group"), [])


class _UserOrdering(filters.FilterSet):
    ordering = OrderingFilter(
        fields={"name": "username", "seen": "last_login"}, tie_break=("-pk",)
    )

    class Meta:
        model = User
        fields = []


class OrderingFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        User.objects.create_user(username="b", last_login=now)
        User.objects.create_user(username="a")
        User.objects.create_user(username="c", last_login=now)
        User.objects.create_user(username="d")

    def _names(self, query):
        filterset = _UserOrdering(
            QueryDict(query), queryset=User.objects.order_by("pk")
        )
        self.assertTrue(filterset.is_valid(), filterset.errors)
        return list(filterset.qs.values_list("username", flat=True))

    def test_absent_keeps_the_order_of_the_queryset(self):
        self.assertEqual(self._names(""), ["b", "a", "c", "d"])

    def test_one_field_either_way(self):
        self.assertEqual(self._names("ordering=name"), ["a", "b", "c", "d"])
        self.assertEqual(self._names("ordering=-name"), ["d", "c", "b", "a"])

    def test_ties_fall_to_the_tie_break_and_empty_values_sort_last(self):
        # b and c share a login time, a and d have none.
        self.assertEqual(self._names("ordering=seen"), ["c", "b", "d", "a"])
        self.assertEqual(self._names("ordering=-seen"), ["c", "b", "d", "a"])

    def test_a_field_that_was_not_declared_is_refused(self):
        for query in ("ordering=email", "ordering=name,seen", "ordering=--name"):
            with self.subTest(query=query):
                filterset = _UserOrdering(QueryDict(query), queryset=User.objects.all())
                self.assertFalse(filterset.is_valid())
                self.assertEqual(list(filterset.errors), ["ordering"])
