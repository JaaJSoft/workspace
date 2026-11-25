"""The rule form's fields and operators, as the page hands them to the browser."""

from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy

from .schema import BOOL_OPS, FIELD_KINDS

FIELD_LABELS = {
    "from": _("From"),
    "to": _("To"),
    "cc": _("Cc"),
    "recipient": _("To or Cc"),
    "subject": _("Subject"),
    "body": _("Body"),
    "folder": _("Folder"),
    "has_attachments": _("Has attachments"),
    "is_starred": _("Is starred"),
    "date": _("Date"),
}

OP_LABELS = {
    "contains": pgettext_lazy("mail rule operator", "contains"),
    "equals": pgettext_lazy("mail rule operator", "equals"),
    "starts_with": pgettext_lazy("mail rule operator", "starts with"),
    "ends_with": pgettext_lazy("mail rule operator", "ends with"),
    "matches_regex": pgettext_lazy("mail rule operator", "matches regex"),
    "in_list": pgettext_lazy("mail rule operator", "in list"),
    "is_true": pgettext_lazy("mail rule operator", "is true"),
    "is_false": pgettext_lazy("mail rule operator", "is false"),
    "greater_than": pgettext_lazy("mail rule operator", "greater than"),
    "less_than": pgettext_lazy("mail rule operator", "less than"),
}


def condition_fields():
    """Every condition field with the operators it accepts, labelled, in form order."""
    return [
        {
            "value": field,
            "label": str(FIELD_LABELS[field]),
            "ops": [
                {
                    "value": op,
                    "label": str(OP_LABELS[op]),
                    "takes_value": op not in BOOL_OPS,
                }
                for op in ops
            ],
        }
        for fields, ops in FIELD_KINDS
        for field in fields
    ]
