from rest_framework import serializers


class CaseInsensitiveSlugRelatedField(serializers.SlugRelatedField):
    """
    SlugRelatedField that resolves the target instance using case-insensitive lookup on the slug_field.
    This preserves the default error messages and behavior of DRF while changing
    only the lookup to use __iexact.
    """

    def to_internal_value(self, data):
        queryset = self.get_queryset()
        if queryset is None:
            raise AssertionError("CaseInsensitiveSlugRelatedField requires a queryset")
        try:
            return queryset.get(**{f"{self.slug_field}__iexact": data})
        except queryset.model.DoesNotExist:
            self.fail("does_not_exist", slug_name=self.slug_field, value=str(data))
        except queryset.model.MultipleObjectsReturned:
            # Ambiguous reference because multiple objects match case-insensitively
            self.fail("invalid")
        # Unreachable: self.fail() always raises ValidationError. Present so every
        # path terminates explicitly (no implicit None return).
        raise AssertionError("unreachable")  # pragma: no cover
