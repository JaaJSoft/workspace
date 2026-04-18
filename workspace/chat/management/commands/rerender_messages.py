from django.core.management.base import BaseCommand

from workspace.chat.models import Message
from workspace.chat.services.rendering import render_message_body, extract_mentions


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

        updated = 0
        for msg in qs.iterator():
            usernames, has_everyone = extract_mentions(msg.body)
            mention_map = {}
            if usernames or has_everyone:
                users = User.objects.filter(
                    username__in=usernames, is_active=True,
                ).values_list('username', 'id')
                mention_map = dict(users)
                if has_everyone:
                    mention_map['everyone'] = None

            new_html = render_message_body(msg.body, mention_map or None)
            if new_html != msg.body_html:
                Message.objects.filter(pk=msg.pk).update(body_html=new_html)
                updated += 1

        self.stdout.write(self.style.SUCCESS(f"Done. Updated: {updated}/{total}"))
