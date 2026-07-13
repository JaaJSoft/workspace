from django.apps import AppConfig
from django.db import connections
from django.db.models.signals import post_migrate

_SQLITE_FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS mail_message_fts_ai AFTER INSERT ON mail_mailmessage BEGIN
  INSERT INTO mail_message_fts(rowid, subject, snippet, from_email, from_name)
  VALUES (new.rowid, new.subject, new.snippet, new.from_email, new.from_name);
END;
CREATE TRIGGER IF NOT EXISTS mail_message_fts_ad AFTER DELETE ON mail_mailmessage BEGIN
  INSERT INTO mail_message_fts(mail_message_fts, rowid, subject, snippet, from_email, from_name)
  VALUES ('delete', old.rowid, old.subject, old.snippet, old.from_email, old.from_name);
END;
CREATE TRIGGER IF NOT EXISTS mail_message_fts_au AFTER UPDATE ON mail_mailmessage BEGIN
  INSERT INTO mail_message_fts(mail_message_fts, rowid, subject, snippet, from_email, from_name)
  VALUES ('delete', old.rowid, old.subject, old.snippet, old.from_email, old.from_name);
  INSERT INTO mail_message_fts(rowid, subject, snippet, from_email, from_name)
  VALUES (new.rowid, new.subject, new.snippet, new.from_email, new.from_name);
END;
INSERT INTO mail_message_fts(mail_message_fts) VALUES ('rebuild');
"""


def rebuild_sqlite_fts(sender, using, **kwargs):
    """Recreate FTS triggers and reindex after a migration.

    Django's SQLite schema changes recreate tables (create-copy-drop-rename),
    which silently drops attached triggers. This restores them idempotently.
    """
    conn = connections[using]
    if conn.vendor != "sqlite":
        return
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='mail_message_fts'"
        )
        if cursor.fetchone() is None:
            return
        cursor.executescript(_SQLITE_FTS_TRIGGERS)


class MailConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "workspace.mail"
    label = "mail"

    def ready(self):
        post_migrate.connect(rebuild_sqlite_fts, sender=self)

        from workspace.core.module_registry import (
            CommandInfo,
            ModuleInfo,
            PendingActionProviderInfo,
            SearchProviderInfo,
            registry,
        )
        from workspace.mail.search import search_contacts, search_mail

        registry.register(
            ModuleInfo(
                name="Mail",
                slug="mail",
                description="Read and send emails from external mail accounts.",
                icon="mail",
                color="warning",
                url="/mail",
                order=25,
            )
        )

        registry.register_search_provider(
            SearchProviderInfo(
                slug="mail",
                module_slug="mail",
                search_fn=search_mail,
            )
        )

        registry.register_search_provider(
            SearchProviderInfo(
                slug="mail-contacts",
                module_slug="mail",
                search_fn=search_contacts,
            )
        )

        def _mail_pending_actions(user):
            from workspace.mail.models import MailMessage
            from workspace.mail.queries import user_account_ids

            return MailMessage.objects.filter(
                account_id__in=user_account_ids(user),
                account__is_active=True,
                is_read=False,
                deleted_at__isnull=True,
            ).count()

        registry.register_pending_action_provider(
            PendingActionProviderInfo(
                module_slug="mail",
                pending_action_fn=_mail_pending_actions,
            )
        )

        registry.register_commands(
            [
                CommandInfo(
                    name="Mail",
                    keywords=["mail", "email", "inbox"],
                    icon="mail",
                    color="warning",
                    url="/mail",
                    kind="navigate",
                    module_slug="mail",
                    order=25,
                ),
                CommandInfo(
                    name="New email",
                    keywords=["new email", "compose", "send"],
                    icon="mail-plus",
                    color="warning",
                    url="/mail?compose=",
                    kind="action",
                    module_slug="mail",
                    order=26,
                ),
            ]
        )

        from workspace.ai.tool_registry import tool_registry
        from workspace.mail.ai_tools import MailToolProvider

        tool_registry.register_provider(MailToolProvider())

        from workspace.core.activity_registry import (
            ActivityProviderInfo,
            activity_registry,
        )
        from workspace.mail.activity import MailActivityProvider

        activity_registry.register(
            ActivityProviderInfo(
                slug="mail",
                label="Mail",
                icon="mail",
                color="warning",
                provider_cls=MailActivityProvider,
            )
        )

        import workspace.mail.signals  # noqa: F401
