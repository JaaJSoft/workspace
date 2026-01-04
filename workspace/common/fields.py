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


# class CachedCaseInsensitiveSlugRelatedField(serializers.SlugRelatedField):
#     """
#     SlugRelatedField that uses cached referentials instead of DB queries.
#     Supports RefPersonType, RefAttributeType, RefAttributeValueType, and RefLinkType.
#
#     Usage:
#         type = CachedCaseInsensitiveSlugRelatedField(
#             slug_field='name',
#             cache_getter='get_person_type_by_name'
#         )
#     """
#
#     def __init__(self, cache_getter=None, **kwargs):
#         """
#         Args:
#             cache_getter: Name of the cache function to use (e.g., 'get_person_type_by_name')
#         """
#         self.cache_getter_name = cache_getter
#         super().__init__(**kwargs)
#
#     def to_internal_value(self, data):
#         if not self.cache_getter_name:
#             # Fallback to parent behavior if no cache getter specified
#             return super().to_internal_value(data)
#
#         try:
#             cache_getter = getattr(ref_cache, self.cache_getter_name, None)
#
#             if cache_getter is None:
#                 # Fallback to DB query if cache function doesn't exist
#                 queryset = self.get_queryset()
#                 if queryset is None:
#                     raise AssertionError("CachedCaseInsensitiveSlugRelatedField requires a queryset")
#                 return queryset.get(**{f"{self.slug_field}__iexact": data})
#
#             # Use cache
#             return cache_getter(data)
#
#         except Exception as e:
#             # Get the model class for proper error message
#             queryset = self.get_queryset()
#             if queryset and hasattr(e, '__class__') and 'DoesNotExist' in e.__class__.__name__:
#                 self.fail("does_not_exist", slug_name=self.slug_field, value=str(data))
#             raise
