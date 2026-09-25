from django.core.management.base import BaseCommand

from workspace.common.management.declarations import load_declaration, write_blocks
from workspace.common.search.schema import DerivedFulltextIndex, FulltextIndex


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
        index = load_declaration(
            options["dotted_path"], (FulltextIndex, DerivedFulltextIndex)
        )
        write_blocks(
            self.stdout,
            (
                ("PG_FORWARD", index.pg_forward_sql()),
                ("PG_REVERSE", index.pg_reverse_sql()),
                ("SQLITE_FORWARD", index.sqlite_forward_sql()),
                ("SQLITE_REVERSE", index.sqlite_reverse_sql()),
            ),
        )
