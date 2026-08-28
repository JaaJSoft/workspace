from django.apps import AppConfig
from django.db.models.signals import post_migrate


class CommonConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.common"

    def ready(self):
        from django.core.checks import register

        from workspace.common.search.checks import check_sqlite_fts_support
        from workspace.common.search.schema import rebuild_sqlite_fts_indexes

        register(check_sqlite_fts_support)

        # Django emits every post_migrate signal at the end of the whole
        # migrate command, so this single hookup runs after all apps'
        # migrations regardless of app order.
        post_migrate.connect(rebuild_sqlite_fts_indexes, sender=self)
