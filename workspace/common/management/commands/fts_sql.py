from django.core.management.base import BaseCommand, CommandError
from django.utils.module_loading import import_string


class Command(BaseCommand):
    help = (
        "Print migration SQL for a FulltextIndex declaration. The output is "
        "meant to be pasted as literal strings into a migration file, so the "
        "applied migration never changes meaning if the declaration evolves."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "dotted_path",
            help="e.g. workspace.mail.search.MAIL_FTS",
        )

    def handle(self, *args, **options):
        try:
            index = import_string(options["dotted_path"])
        except ImportError as exc:
            raise CommandError(str(exc)) from exc
        blocks = (
            ("PG_FORWARD", index.pg_forward_sql()),
            ("PG_REVERSE", index.pg_reverse_sql()),
            ("SQLITE_FORWARD", index.sqlite_forward_sql()),
            ("SQLITE_REVERSE", index.sqlite_reverse_sql()),
        )
        for title, sql in blocks:
            self.stdout.write(f"-- {title}")
            self.stdout.write(sql)
            self.stdout.write("")
