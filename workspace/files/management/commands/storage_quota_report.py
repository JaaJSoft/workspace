"""Recompute every storage bucket and report the ones over their limit."""

import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.template.defaultfilters import filesizeformat

from workspace.files.services.quota import (
    effective_group_quota,
    effective_quota,
    group_usage,
    personal_usage,
)

User = get_user_model()


class Command(BaseCommand):
    help = (
        "List storage usage per bucket (users and group folders) against the "
        "quota that applies to it."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--over",
            action="store_true",
            help="Only list buckets that exceed their limit; exit 1 if any does.",
        )

    def handle(self, *args, **options):
        only_over = options["over"]
        rows = []
        for user in User.objects.order_by("username").iterator():
            rows.append(
                (
                    "user",
                    user.username,
                    personal_usage(user.pk),
                    effective_quota(user.pk),
                )
            )
        for group in Group.objects.order_by("name").iterator():
            rows.append(
                (
                    "group",
                    group.name,
                    group_usage(group.pk),
                    effective_group_quota(group.pk),
                )
            )

        over = [r for r in rows if r[3] is not None and r[2] > r[3]]
        listed = over if only_over else sorted(rows, key=lambda r: r[2], reverse=True)

        for kind, name, used, limit in listed:
            limit_label = "unlimited" if limit is None else filesizeformat(limit)
            flag = " OVER" if limit is not None and used > limit else ""
            self.stdout.write(
                f"{kind:5} {name:30} {filesizeformat(used):>12} / {limit_label}{flag}"
            )

        if not listed:
            self.stdout.write(
                "No bucket over its limit." if only_over else "No bucket."
            )

        self.stdout.write(f"\n{len(over)} bucket(s) over their limit.")
        if only_over and over:
            sys.exit(1)
