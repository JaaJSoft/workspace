"""Seed/verify a small dataset used by the postgres-migration CI smoke test.

Run on the source SQLite (``--seed``) to create representative rows that
exercise the cross-DB code paths fixed in the migration command:

* a few ``auth.User`` rows + a ``UserPresence`` row (its pk is a FK to user;
  loaddata needs to keep that pk in sync after sequences reset)
* a ``MailAccount`` (its ``post_save`` signal seeds five labels - the
  signal must respect ``raw=True`` during loaddata, otherwise the labels
  duplicate)
* one custom ``MailLabel`` (verifies fixture-loaded rows survive)
* a ``MailMessage`` with an accented subject (verifies the generated
  ``search_tsv`` column populates during loaddata and ``f_unaccent`` makes
  the GIN index accent-insensitive)
* a chat ``Message`` with an accented body (verifies the chat generated
  tsvector column also populates during loaddata)
* a ``Task`` and an accented-title ``Event`` (verify the projects and
  calendar generated tsvector columns also populate during loaddata)
* a couple of files + a calendar event (sanity-check UUID PKs)

Run on the target ``DATABASE_URL`` (``--verify``) after
``migrate_to_postgres`` to assert every seeded row migrated and that no
duplicates were created by signals firing on raw=True.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

SEED_USERS = [
    {"username": "pg_smoke_alice", "email": "alice@example.com"},
    {"username": "pg_smoke_bob", "email": "bob@example.com"},
]
SEED_MAIL_EMAIL = "smoke@example.com"
SEED_CUSTOM_LABEL = "SmokeCustom"
SEED_MAIL_SUBJECT = "Résumé du projet Alpha"
# "prévisionnel" appears ONLY in the body, never in subject/snippet/from,
# so finding it proves the body is part of the index.
SEED_MAIL_BODY = "Le budget prévisionnel est joint pour relecture."
# "déploiement" appears ONLY in this chat body across all seeds, so an
# accent-less hit proves the chat tsvector populated during loaddata.
SEED_CHAT_BODY = "Le déploiement de vendredi est confirmé."
SEED_PROJECT_NAME = "Refonte du tableau de bord"
SEED_TASK_TITLE = "Préparer la maquette"
SEED_EVENT_TITLE = "Réunion de lancement"


class Command(BaseCommand):
    help = "Seed or verify data for the postgres migration CI smoke test."

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--seed", action="store_true", help="Populate fresh data.")
        group.add_argument(
            "--verify", action="store_true", help="Assert data matches expectations."
        )

    def handle(self, *args, **options):
        if options["seed"]:
            self._seed()
        else:
            self._verify()

    def _seed(self):
        from workspace.calendar.models import Calendar, Event
        from workspace.files.models import File
        from workspace.mail.models import (
            MailAccount,
            MailFolder,
            MailLabel,
            MailMessage,
        )
        from workspace.users.models import UserPresence

        User = get_user_model()
        alice = User.objects.create_user(**SEED_USERS[0], password="pw-1234")
        bob = User.objects.create_user(**SEED_USERS[1], password="pw-1234")
        UserPresence.objects.create(user=alice, last_seen=timezone.now())

        account = MailAccount.objects.create(
            owner=alice,
            email=SEED_MAIL_EMAIL,
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username=SEED_MAIL_EMAIL,
        )
        MailLabel.objects.create(account=account, name=SEED_CUSTOM_LABEL, color="info")

        inbox = MailFolder.objects.create(
            account=account,
            name="INBOX",
            display_name="Inbox",
            folder_type="inbox",
        )
        MailMessage.objects.create(
            account=account,
            folder=inbox,
            imap_uid=1,
            subject=SEED_MAIL_SUBJECT,
            snippet="notes de réunion",
            body_text=SEED_MAIL_BODY,
        )

        from workspace.chat.models import Conversation, ConversationMember, Message

        conv = Conversation.objects.create(
            kind="group", title="Smoke Chat", created_by=alice
        )
        ConversationMember.objects.create(conversation=conv, user=alice)
        Message.objects.create(conversation=conv, author=alice, body=SEED_CHAT_BODY)

        from workspace.projects.models import Project, ProjectMember, Task, TaskStatus

        project = Project.objects.create(name=SEED_PROJECT_NAME, created_by=alice)
        ProjectMember.objects.create(
            project=project, user=alice, role=ProjectMember.Role.ADMIN
        )
        task_status = TaskStatus.objects.create(
            project=project, name="Todo", category=TaskStatus.Category.BACKLOG
        )
        Task.objects.create(
            project=project, title=SEED_TASK_TITLE, status=task_status, created_by=alice
        )

        smoke_cal = Calendar.objects.create(name="Smoke", owner=alice)
        Event.objects.create(
            calendar=smoke_cal,
            owner=alice,
            title=SEED_EVENT_TITLE,
            start=timezone.now(),
        )

        cal = Calendar.objects.create(owner=alice, name="Smoke Cal")
        Event.objects.create(
            calendar=cal,
            owner=alice,
            title="Smoke Event",
            start=timezone.now(),
            end=timezone.now(),
        )

        # Two file rows: one user file, one folder.
        folder = File.objects.create(name="smoke-folder", owner=bob, node_type="folder")
        File.objects.create(
            name="smoke.txt", owner=bob, node_type="file", parent=folder
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded users={User.objects.count()}, "
                f"mail_account={MailAccount.objects.count()}, "
                f"labels={MailLabel.objects.count()}, "
                f"messages={MailMessage.objects.count()}"
            )
        )

    def _verify(self):
        from workspace.calendar.models import Event
        from workspace.files.models import File
        from workspace.mail.models import MailAccount, MailLabel
        from workspace.users.models import UserPresence

        User = get_user_model()
        alice = User.objects.get(username=SEED_USERS[0]["username"])
        bob = User.objects.get(username=SEED_USERS[1]["username"])

        if alice.email != SEED_USERS[0]["email"]:
            raise CommandError(f"alice.email mismatch: {alice.email}")
        if not UserPresence.objects.filter(user=alice).exists():
            raise CommandError("UserPresence row for alice missing")

        account = MailAccount.objects.get(email=SEED_MAIL_EMAIL)
        # 5 seeded by signal + 1 custom = 6, exactly. Duplicates from signal
        # firing on raw=True would land here as 11 (5 + 5 + 1).
        label_count = account.labels.count()
        if label_count != 6:
            names = list(account.labels.values_list("name", flat=True))
            raise CommandError(
                f"Expected 6 labels on {SEED_MAIL_EMAIL}, got {label_count}: {names}"
            )

        if not MailLabel.objects.filter(
            account=account, name=SEED_CUSTOM_LABEL
        ).exists():
            raise CommandError(f"Custom label {SEED_CUSTOM_LABEL} missing")

        if not Event.objects.filter(title="Smoke Event").exists():
            raise CommandError("Smoke Event missing")

        if File.objects.filter(owner=bob).count() != 2:
            raise CommandError("File rows for bob missing")

        # Sequence-reset sanity check: creating a new user should not collide.
        sentinel = User.objects.create_user(username="pg_smoke_sentinel", password="pw")
        if sentinel.pk <= max(alice.pk, bob.pk):
            raise CommandError(
                f"New user pk={sentinel.pk} <= existing max "
                f"({max(alice.pk, bob.pk)}); sequence not reset"
            )

        from workspace.mail.search import search_mail

        # Query without the accent must still match "Résumé" via f_unaccent,
        # proving the generated tsvector column populated during loaddata and
        # the GIN index answers the query.
        hits = search_mail("resume", alice, limit=10)
        subjects = [h.name for h in hits]
        if SEED_MAIL_SUBJECT not in subjects:
            raise CommandError(
                "FTS verify failed: seeded accented message not found via "
                f"full-text search on PostgreSQL. Got: {subjects}"
            )

        self.stdout.write(self.style.SUCCESS("  FTS accent-insensitive search OK"))

        # Same message, but through a word that exists only in the body
        # (accent-insensitive too): proves body_text made it into the
        # generated tsvector on PostgreSQL.
        hits = search_mail("previsionnel", alice, limit=10)
        subjects = [h.name for h in hits]
        if SEED_MAIL_SUBJECT not in subjects:
            raise CommandError(
                "FTS verify failed: body-only word not found via full-text "
                f"search on PostgreSQL. Got: {subjects}"
            )

        self.stdout.write(self.style.SUCCESS("  FTS body search OK"))

        from workspace.chat.search import search_chat_messages

        hits = search_chat_messages("deploiement", alice, 10)
        if not any("déploiement" in h.matched_value for h in hits):
            raise CommandError(
                "FTS verify failed: chat body word not found via full-text "
                f"search on PostgreSQL. Got: {[h.matched_value for h in hits]}"
            )

        self.stdout.write(self.style.SUCCESS("  FTS chat body search OK"))

        from workspace.projects.services.search import (
            search_projects_qs,
            search_tasks_qs,
        )

        # Query without the accent must still match "Préparer" via f_unaccent,
        # proving the projects_task tsvector populated during loaddata.
        task_hits = [t.title for t in search_tasks_qs(alice, "preparer")]
        if SEED_TASK_TITLE not in task_hits:
            raise CommandError(
                "FTS verify failed: seeded task not found via full-text "
                f"search on PostgreSQL. Got: {task_hits}"
            )

        self.stdout.write(self.style.SUCCESS("  FTS task search OK"))

        project_hits = [p.name for p in search_projects_qs(alice, "tableau")]
        if SEED_PROJECT_NAME not in project_hits:
            raise CommandError(
                "FTS verify failed: seeded project not found via full-text "
                f"search on PostgreSQL. Got: {project_hits}"
            )

        self.stdout.write(self.style.SUCCESS("  FTS project search OK"))

        from workspace.calendar.services.event_search import search_events_qs

        # Query without the accent must still match "Réunion" via
        # f_unaccent, proving the generated tsvector column populated
        # during loaddata and the GIN index answers the query.
        event_hits = [e.title for e in search_events_qs(alice, "reunion")]
        if SEED_EVENT_TITLE not in event_hits:
            raise CommandError(
                "FTS verify failed: accented event title not found via "
                f"full-text search on PostgreSQL. Got: {event_hits}"
            )

        self.stdout.write(self.style.SUCCESS("  FTS event search OK"))
        self.stdout.write(self.style.SUCCESS("PostgreSQL migration smoke test OK"))
