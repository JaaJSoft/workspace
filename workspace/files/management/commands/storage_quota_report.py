"""Recompute every storage bucket and report the ones over their limit."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Sum
from django.template.defaultfilters import filesizeformat

from workspace.files.models import File, GroupStorageQuota, UserStorageQuota

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
        rows = [*self._personal_rows(), *self._group_rows()]

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
            raise CommandError(f"{len(over)} bucket(s) over their limit.", returncode=1)

    # Six queries whatever the number of accounts: per bucket kind, one usage
    # aggregate, one full read of the (small) override table, and the names to
    # print. The per-bucket helpers in services.quota cost two queries each,
    # which turns a report over a few thousand accounts into as many round
    # trips.

    def _personal_rows(self):
        usage = dict(
            File.objects.filter(group__isnull=True, node_type=File.NodeType.FILE)
            .values_list("owner_id")
            .annotate(total=Sum("size"))
        )
        limits = dict(UserStorageQuota.objects.values_list("user_id", "quota_bytes"))
        return [
            (
                "user",
                username,
                usage.get(pk) or 0,
                limits.get(pk, settings.STORAGE_QUOTA_BYTES),
            )
            for pk, username in User.objects.order_by("username").values_list(
                "pk", "username"
            )
        ]

    def _group_rows(self):
        usage = dict(
            File.objects.filter(group__isnull=False, node_type=File.NodeType.FILE)
            .values_list("group_id")
            .annotate(total=Sum("size"))
        )
        limits = dict(GroupStorageQuota.objects.values_list("group_id", "quota_bytes"))
        return [
            ("group", name, usage.get(pk) or 0, limits.get(pk))
            for pk, name in Group.objects.order_by("name").values_list("pk", "name")
        ]
