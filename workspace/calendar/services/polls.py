"""Scheduling polls: proposing times, voting on them, choosing one.

A poll is public to the instance by design - whoever holds its link reads it
and votes - and its share link opens it to people without an account. Only
its creator changes it.
"""

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import pgettext

from workspace.users.queries import pickable_users
from workspace.users.services.settings import get_setting

from ..models import Calendar, Event, EventMember, Poll, PollInvitee, PollSlot, PollVote
from .notifications import notify_in_languages

User = get_user_model()


class PollClosed(Exception):
    """The poll took its decision: it takes no vote and no invitee anymore."""


def with_details(polls):
    """*polls* with what serializing them reads, their votes aside (see
    ``attach_votes``)."""
    return polls.select_related("created_by").prefetch_related(
        Prefetch(
            "slots",
            queryset=PollSlot.objects.annotate(
                yes_count=Count("votes", filter=Q(votes__choice="yes")),
                maybe_count=Count("votes", filter=Q(votes__choice="maybe")),
            ).order_by("position", "start"),
        ),
        Prefetch(
            "invitees",
            queryset=PollInvitee.objects.select_related("user").order_by(
                "created_at", "uuid"
            ),
        ),
    )


def attach_votes(polls):
    """Give each of *polls* its votes as ``poll.cast_votes``, in one query."""
    polls = list(polls)
    votes = {poll.pk: [] for poll in polls}
    for vote in (
        PollVote.objects.filter(slot__poll__in=polls)
        .select_related("user", "slot")
        .order_by("created_at", "uuid")
    ):
        votes[vote.slot.poll_id].append(vote)
    for poll in polls:
        poll.cast_votes = votes[poll.pk]
    return polls


def detailed(poll):
    """*poll* read again, ready to be serialized."""
    return attach_votes([with_details(Poll.objects.filter(pk=poll.pk)).get()])[0]


def involving(user):
    """The polls *user* created, was invited to or voted in."""
    voted = PollVote.objects.filter(user=user).values("slot__poll_id")
    invited = PollInvitee.objects.filter(user=user).values("poll_id")
    return Poll.objects.filter(
        Q(created_by=user) | Q(uuid__in=voted) | Q(uuid__in=invited)
    )


def _write_slots(poll, slots, known=None):
    """Make *poll*'s slots the ones proposed, in the order of their start.

    A proposal naming a slot of *known* keeps that slot and its votes; any
    other is a new slot.
    """
    known = known or {}
    for position, proposed in enumerate(sorted(slots, key=lambda slot: slot["start"])):
        slot = known.get(proposed.get("uuid"))
        if slot is None:
            PollSlot.objects.create(
                poll=poll,
                start=proposed["start"],
                end=proposed.get("end"),
                position=position,
            )
            continue
        slot.start = proposed["start"]
        slot.end = proposed.get("end")
        slot.position = position
        slot.save(update_fields=["start", "end", "position"])


def _set_invitees(poll, actor, user_ids):
    """Make *poll*'s invitees the accounts *user_ids* names.

    Someone newly invited is told; someone no longer invited loses the votes
    they cast. An id naming nobody who can be invited is left out.
    """
    current = set(poll.invitees.values_list("user_id", flat=True))
    wanted = set(user_ids) - {actor.id}
    removed = current - wanted
    if removed:
        PollInvitee.objects.filter(poll=poll, user_id__in=removed).delete()
        PollVote.objects.filter(slot__poll=poll, user_id__in=removed).delete()
    invited = list(pickable_users().filter(id__in=wanted - current))
    PollInvitee.objects.bulk_create(
        [PollInvitee(poll=poll, user=user) for user in invited],
        ignore_conflicts=True,
    )
    if invited:
        notify_in_languages(
            recipients=invited,
            message=lambda: (
                _('Poll invitation: "%(title)s"') % {"title": poll.title},
                _("%(user)s invited you to vote on a poll.") % {"user": actor.username},
            ),
            url=f"/calendar?poll={poll.pk}",
            actor=actor,
            source=poll,
        )


@transaction.atomic
def create_poll(creator, *, title, slots, description="", invitees=()):
    poll = Poll.objects.create(title=title, description=description, created_by=creator)
    _write_slots(poll, slots)
    if invitees:
        _set_invitees(poll, creator, invitees)
    return poll


@transaction.atomic
def update_poll(poll, actor, changes):
    """Write *changes* (title, description, slots, invitees) to *poll*.

    Raises ``PollClosed`` for a change of invitees once a time was chosen.
    """
    if "invitees" in changes and poll.status != Poll.Status.OPEN:
        raise PollClosed

    written = [field for field in ("title", "description") if field in changes]
    for field in written:
        setattr(poll, field, changes[field])
    # Written even when nothing of its own changed: a vote locks this row,
    # so a slot removed below is gone for the vote that follows.
    poll.save(update_fields=[*written, "updated_at"])

    if "slots" in changes:
        known = {slot.uuid: slot for slot in poll.slots.all()}
        kept = {proposed.get("uuid") for proposed in changes["slots"]}
        removed = set(known) - kept
        voters = []
        if removed:
            # Deleting a slot deletes its votes: read who cast them first.
            voters = list(
                User.objects.filter(poll_votes__slot__uuid__in=removed)
                .exclude(id=actor.id)
                .distinct()
            )
            PollSlot.objects.filter(uuid__in=removed).delete()
        _write_slots(poll, changes["slots"], known)
        if voters:
            notify_in_languages(
                recipients=voters,
                message=lambda: (
                    _('Poll updated: "%(title)s"') % {"title": poll.title},
                    _("Time slots have been updated. Please review your votes."),
                ),
                url=f"/calendar?poll={poll.pk}",
                actor=actor,
                source=poll,
            )

    if "invitees" in changes:
        _set_invitees(poll, actor, changes["invitees"])
    return poll


def _lock_open_poll(poll):
    """Lock *poll*'s row for the rest of the transaction; ``PollClosed``
    once a time was chosen.

    Votes are written under this lock, so a finalize either waits for them
    or has already closed the poll and the vote is refused like any vote on
    a closed poll.
    """
    still_open = Poll.objects.select_for_update().filter(
        pk=poll.pk, status=Poll.Status.OPEN
    )
    if not still_open.exists():
        raise PollClosed


def cast_votes(
    poll, votes, *, user=None, voter_token="", guest_name="", guest_email=""
):
    """Record *votes* (``[{slot, choice}]``) of *user*, or of the guest
    *voter_token* stands for. Raises ``PollClosed`` once a time was chosen.

    A vote naming a slot the poll does not have is left out: the creator may
    have removed it while the voter was choosing.
    """
    with transaction.atomic():
        _lock_open_poll(poll)
        # Read under that lock: the poll edit writes the poll row before it
        # deletes slots, so a slot it removed is already gone here.
        live = set(
            poll.slots.filter(uuid__in={vote["slot"] for vote in votes}).values_list(
                "uuid", flat=True
            )
        )
        for vote in votes:
            if vote["slot"] not in live:
                continue
            if user is not None:
                PollVote.objects.update_or_create(
                    slot_id=vote["slot"],
                    user=user,
                    defaults={"choice": vote["choice"]},
                )
            else:
                PollVote.objects.update_or_create(
                    slot_id=vote["slot"],
                    user=None,
                    voter_token=voter_token,
                    defaults={
                        "choice": vote["choice"],
                        "guest_name": guest_name,
                        "guest_email": guest_email,
                    },
                )

    creator = poll.created_by
    if user is not None and creator.id == user.id:
        return
    preferences = get_setting(creator, "calendar", "preferences", default={})
    if not preferences.get("notifyPollVotes", True):
        return
    voter = user.username if user is not None else guest_name

    def new_vote():
        if voter:
            body = _("%(user)s voted on your poll.") % {"user": voter}
        else:
            body = _("A guest voted on your poll.")
        return _('New vote on "%(title)s"') % {"title": poll.title}, body

    notify_in_languages(
        recipients=[creator],
        message=new_vote,
        url=f"/calendar?poll={poll.pk}",
        actor=user,
        source=poll,
    )


def _calendar_for_the_event(owner):
    """A calendar *owner* can edit: never one an external feed or a mail
    account writes into, whose next sync owns it."""
    calendar = (
        Calendar.objects.filter(
            owner=owner, external_source__isnull=True, mail_account__isnull=True
        )
        .order_by("name")
        .first()
    )
    if calendar:
        return calendar
    return Calendar.objects.create(
        owner=owner,
        name=pgettext("default calendar name", "Personal"),
        color="primary",
    )


@transaction.atomic
def finalize_poll(poll, actor, slot):
    """Choose *slot*: close *poll* and create the event at that time.

    Whoever answered yes or maybe to the slot is on the event, accepted.
    Raises ``PollClosed`` when a time was chosen already.
    """
    # The transition is the guard: of two concurrent finalizes only one
    # closes the poll, and the other finds it closed instead of creating a
    # second event.
    closed = Poll.objects.filter(pk=poll.pk, status=Poll.Status.OPEN).update(
        status=Poll.Status.CLOSED, chosen_slot=slot, updated_at=timezone.now()
    )
    if not closed:
        raise PollClosed

    event = Event.objects.create(
        calendar=_calendar_for_the_event(actor),
        title=poll.title,
        description=poll.description,
        start=slot.start,
        end=slot.end,
        owner=actor,
    )
    attending = list(
        PollVote.objects.filter(
            slot=slot, choice__in=["yes", "maybe"], user__isnull=False
        )
        .exclude(user=actor)
        .values_list("user_id", flat=True)
        .distinct()
    )
    EventMember.objects.bulk_create(
        [
            EventMember(event=event, user_id=uid, status=EventMember.Status.ACCEPTED)
            for uid in attending
        ]
    )
    Poll.objects.filter(pk=poll.pk).update(event=event)

    # Only yes/maybe voters are on the event; the other invitees cannot open
    # it, so their notification is keyed on (and links to) the poll.
    invited = list(
        User.objects.filter(poll_invitations__poll=poll).exclude(id=actor.id)
    )

    def finalized():
        return (
            _('Poll finalized: "%(title)s"') % {"title": poll.title},
            _("A date has been chosen."),
        )

    on_the_event = [user for user in invited if user.id in set(attending)]
    others = [user for user in invited if user.id not in set(attending)]
    if on_the_event:
        notify_in_languages(
            recipients=on_the_event,
            message=finalized,
            url=f"/calendar?event={event.pk}",
            actor=actor,
            source=event,
        )
    if others:
        notify_in_languages(
            recipients=others,
            message=finalized,
            url=f"/calendar?poll={poll.pk}",
            actor=actor,
            source=poll,
        )
    return event
