"""Record the other end of every existing link event.

A link event names the task at the other end of the link only by its
reference string, so a feed cannot tell whether its viewer may read it. Both
ends' events are written back to back by the same call, each naming the
other's number: pairing them recovers the other end's project (and task, when
it still exists) without trusting the reference's key, which may have been
renamed or reused since. An event left unpaired keeps a null ``from_ref`` and
is hidden from every feed.
"""

from collections import defaultdict
from datetime import timedelta

from django.db import migrations

_LINK_TYPES = ("linked", "unlinked")
# Both events of a pair come from one transaction, microseconds apart.
_PAIR_WINDOW = timedelta(seconds=2)


def _names(event, other):
    return event["to_value"].rpartition("-")[2] == str(other["task_number"])


def _is_pair(event, other):
    return (
        other["type"] == event["type"]
        and other["actor_id"] == event["actor_id"]
        and _names(event, other)
        and _names(other, event)
    )


def backfill_link_event_refs(apps, schema_editor):
    TaskEvent = apps.get_model("projects", "TaskEvent")
    db = schema_editor.connection.alias
    events = list(
        TaskEvent.objects.using(db)
        .filter(type__in=_LINK_TYPES, from_ref__isnull=True, task_number__isnull=False)
        .order_by("created_at", "uuid")
        .values(
            "uuid",
            "project_id",
            "task_id",
            "task_number",
            "actor_id",
            "type",
            "to_value",
            "created_at",
        )
    )
    # A pair shares its type and actor, so each bucket is scanned on its own
    # and one actor's burst of links does not lengthen anyone else's scan.
    buckets = defaultdict(list)
    for event in events:
        buckets[event["type"], event["actor_id"]].append(event)
    partner = {}
    for bucket in buckets.values():
        candidates = defaultdict(list)
        for index, event in enumerate(bucket):
            for other_index in range(index + 1, len(bucket)):
                other = bucket[other_index]
                if other["created_at"] - event["created_at"] > _PAIR_WINDOW:
                    break
                if _is_pair(event, other):
                    candidates[event["uuid"]].append(other)
                    candidates[other["uuid"]].append(event)
        # Task numbers repeat across projects, so two links made at once can
        # each name the other's numbers. Guessing would record the wrong
        # project and open the event to the wrong readers: an event whose
        # partner is not unambiguous stays unresolved, hidden from everyone.
        for uuid, matches in candidates.items():
            if len(matches) == 1 and len(candidates[matches[0]["uuid"]]) == 1:
                partner[uuid] = matches[0]
    TaskEvent.objects.using(db).bulk_update(
        [
            TaskEvent(uuid=uuid, from_ref=other["project_id"], to_ref=other["task_id"])
            for uuid, other in partner.items()
        ],
        ["from_ref", "to_ref"],
        batch_size=500,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("projects", "0029_task_file_link"),
    ]

    operations = [
        migrations.RunPython(backfill_link_event_refs, migrations.RunPython.noop),
    ]
