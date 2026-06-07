"""Seed/verify a small dataset used by the postgres-migration CI smoke test.

Run on the source SQLite (``--seed``) to create representative rows that
exercise the cross-DB code paths fixed in the migration command:

* a few ``auth.User`` rows + a ``UserPresence`` row (its pk is a FK to user;
  loaddata needs to keep that pk in sync after sequences reset)
* a ``MailAccount`` (its ``post_save`` signal seeds five labels - the
  signal must respect ``raw=True`` during loaddata, otherwise the labels
  duplicate)
* one custom ``MailLabel`` (verifies fixture-loaded rows survive)
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
        from workspace.mail.models import MailAccount, MailLabel
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
                f"labels={MailLabel.objects.count()}"
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

        self.stdout.write(self.style.SUCCESS("PostgreSQL migration smoke test OK"))
