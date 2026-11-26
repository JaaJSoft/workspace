from django_filters import rest_framework as filters

from workspace.common.filters import BooleanFilter, MultiValueFilter, TextSearchFilter

from .models import Notification


class NotificationFilter(filters.FilterSet):
    q = TextSearchFilter(fields=("title", "body"))
    origin = MultiValueFilter()
    read = BooleanFilter(field_name="read_at", lookup_expr="isnull", exclude=True)

    class Meta:
        model = Notification
        fields = []
