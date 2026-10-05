"""What a username may be, beyond the characters Django's validator allows."""

from django.core.exceptions import ValidationError

# A username is a segment of its owner's storage paths
# (files/users/<username>/...), so "." or ".." would root that tree on the
# users directory itself, or above it.
PATH_SEGMENT_USERNAMES = frozenset({".", ".."})


def validate_username(value):
    if value in PATH_SEGMENT_USERNAMES:
        raise ValidationError(
            "“%(value)s” cannot be used as a username.",
            code="reserved_username",
            params={"value": value},
        )
