from django.core.management.base import BaseCommand

from workspace.chat.models import Message
from workspace.chat.services.mentions import build_mention_map
from workspace.chat.services.rendering import render_message_body


class Command(BaseCommand):
    help = "Re-render body_html for all non-deleted chat messages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show count without writing changes.",
        )

    def handle(self, *args, **options):
        from django.contrib.auth import get_user_model

        User = get_user_model()

        dry_run = options["dry_run"]
        qs = Message.objects.filter(deleted_at__isnull=True).exclude(body="")
        total = qs.count()
        self.stdout.write(f"Messages to re-render: {total}")

        if dry_run:
            return

        active_users = User.objects.filter(is_active=True)
        updated = 0
        for msg in qs.iterator():
            mention_map, _ = build_mention_map(msg.body, users=active_users)
            new_html = render_message_body(msg.body, mention_map or None)
            if new_html != msg.body_html:
                Message.objects.filter(pk=msg.pk).update(body_html=new_html)
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Done. Updated: {updated}/{total}"))
