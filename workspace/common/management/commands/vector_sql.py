from django.core.management.base import BaseCommand

from workspace.common.management.declarations import load_declaration, write_blocks
from workspace.common.vectors.schema import VectorIndex


class Command(BaseCommand):
    help = (
        "Print migration SQL for a VectorIndex declaration. The output is "
        "meant to be pasted as literals into a RunVectorIndexSQL operation, "
        "so the applied migration never changes meaning if the declaration "
        "evolves."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "dotted_path",
            help="Dotted path to a VectorIndex, e.g. "
            "workspace.common.tests.test_vectors.VECTORS",
        )

    def handle(self, *args, **options):
        index = load_declaration(options["dotted_path"], VectorIndex)
        write_blocks(
            self.stdout,
            (
                ("PG_FORWARD", index.pg_forward_sql()),
                ("PG_INDEX", index.pg_index_sql()),
                ("PG_BACKFILL", repr(index.pg_backfill)),
                ("PG_REVERSE", index.pg_reverse_sql()),
                ("SQLITE_FORWARD", index.sqlite_forward_sql()),
                ("SQLITE_REVERSE", index.sqlite_reverse_sql()),
            ),
        )
