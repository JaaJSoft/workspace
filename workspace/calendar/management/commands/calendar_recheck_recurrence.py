"""Recompute the derived recurrence columns and report any drift.

Derivation lives in a service rather than in ``Event.save()``, so a writer can
in principle skip it. The structural test catches that at build time; this
command catches data that already drifted, and repairs it with --fix.
"""

from django.core.management.base import BaseCommand

from workspace.calendar.models import Event
from workspace.calendar.services.recurrence_rule import apply_rule


class Command(BaseCommand):
    help = "Verify Event.is_recurring / recurrence_until against recurrence_rule."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix", action="store_true", help="Write the recomputed values back."
        )

    def handle(self, *args, **options):
        drifted = []
        for event in Event.objects.iterator():
            before = (event.is_recurring, event.recurrence_until)
            apply_rule(event, event.recurrence_rule)
            if (event.is_recurring, event.recurrence_until) != before:
                drifted.append(event)

        if not drifted:
            self.stdout.write(self.style.SUCCESS("All events consistent."))
            return

        for event in drifted:
            self.stdout.write(f"drift: {event.pk} {event.title!r}")

        if options["fix"]:
            Event.objects.bulk_update(
                drifted, ["is_recurring", "recurrence_until"], batch_size=500
            )
            self.stdout.write(self.style.SUCCESS(f"Repaired {len(drifted)} event(s)."))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f"{len(drifted)} event(s) drifted; re-run with --fix."
                )
            )
