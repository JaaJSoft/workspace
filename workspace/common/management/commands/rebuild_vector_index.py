from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS
from django.utils.module_loading import import_string

from workspace.common.vectors.indexing import rebuild_vector_index
from workspace.common.vectors.schema import VectorIndex, registered_vector_indexes


class Command(BaseCommand):
    help = (
        "Rebuild vector indexes from the vectors stored on their rows: after "
        "installing pgvector or sqlite-vec, after a SQLite to PostgreSQL "
        "migration, or to purge entries of rows deleted behind the index's back."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "dotted_paths",
            nargs="*",
            help="VectorIndex declarations to rebuild; every registered one by default.",
        )
        parser.add_argument("--database", default=DEFAULT_DB_ALIAS)

    def handle(self, *args, **options):
        indexes = [self._load(path) for path in options["dotted_paths"]]
        if not indexes:
            indexes = registered_vector_indexes()
        if not indexes:
            self.stdout.write("No vector index registered.")
            return
        for index in indexes:
            backend = rebuild_vector_index(index, using=options["database"])
            self.stdout.write(f"{index.table}.{index.source_column}: {backend}")

    @staticmethod
    def _load(path):
        try:
            index = import_string(path)
        except ImportError as exc:
            raise CommandError(str(exc)) from exc
        if not isinstance(index, VectorIndex):
            raise CommandError(f"{path} is not a VectorIndex")
        return index
