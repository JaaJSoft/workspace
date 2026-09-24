"""Contacts importer: files the cards of the remote address books the user
picked into the People module.

Cards are fetched in batches but imported one at a time: each card's job item
must name the person it became, or the next run could not tell it from a card
never seen and would import the whole book again.
"""

import base64
import logging
import re
from itertools import batched

from django.conf import settings
from django.db import DataError, IntegrityError, transaction
from rest_framework import serializers
from vobject.vcard import stringToTextValues

from workspace.common.logging import scrub
from workspace.people.models import PersonList
from workspace.people.queries import user_persons
from workspace.people.serializers import SCOPE_MINE, parse_scope
from workspace.people.services.lists import add_members, create_list
from workspace.people.services.vcard import VCardError
from workspace.people.services.vcard_import import import_vcards

from ..models import ImportJobItem
from ..providers.base import KIND_CONTACTS, ProviderError
from ..serializers import RemotePathField
from .base import Importer, JobFailed, Outcome

logger = logging.getLogger(__name__)

# Cards per fetch: round trips against response size.
FETCH_BATCH = 50

_FOLD_RE = re.compile(r"\r?\n[ \t]")
_GROUP_KIND_RE = re.compile(
    r"^(?:X-ADDRESSBOOKSERVER-)?KIND(?:;[^:\r\n]*)?:[ \t]*group[ \t]*\r?$",
    re.IGNORECASE | re.MULTILINE,
)
_PHOTO_URL_RE = re.compile(
    r"^PHOTO(?:;[^:\r\n]*)?:(?P<url>https?://\S+?)[ \t]*(?=\r?$)",
    re.IGNORECASE | re.MULTILINE,
)

_LIST_NAME_MAX = PersonList._meta.get_field("name").max_length

# Subtypes Pillow can decode into an avatar; anything else (svg+xml, heic...)
# stays a link rather than a PHOTO line _apply_photo silently fails on.
_INLINABLE_PHOTO_SUBTYPES = frozenset({"jpeg", "png", "gif", "webp", "bmp", "tiff"})


def unfold(text):
    """The card with its continuation lines joined back (RFC 6350 3.2)."""
    return _FOLD_RE.sub("", text)


def is_group_card(text):
    return _GROUP_KIND_RE.search(unfold(text)) is not None


def categories(person):
    """The category names on the person's card, in order, each once."""
    names = []
    for entry in (person.extra_properties or {}).get("CATEGORIES", []):
        for value in stringToTextValues(entry.get("value", "")):
            name = " ".join(value.split())[:_LIST_NAME_MAX]
            if name and name not in names:
                names.append(name)
    return names


def file_in_category_lists(person, scope):
    """Put the person in one list per category of its card, creating the
    missing lists; returns how many were created."""
    created = 0
    for name in categories(person):
        person_list = PersonList.objects.filter(**scope, name=name).first()
        if person_list is None:
            try:
                with transaction.atomic():
                    person_list = create_list(**scope, name=name)
                created += 1
            except IntegrityError:
                # Another slice created it between the lookup and the insert.
                person_list = PersonList.objects.get(**scope, name=name)
        add_members(person_list, [person])
    return created


def inline_linked_photo(text, fetch_photo):
    """The card with a photo given by URL swapped for the image itself, so the
    People import stores it as the avatar like an inline one. The card is
    returned untouched when it links no photo or the photo cannot be had."""
    unfolded = unfold(text)
    match = _PHOTO_URL_RE.search(unfolded)
    if match is None:
        return text
    fetched = fetch_photo(match.group("url"))
    if fetched is None:
        return text
    data, subtype = fetched
    if subtype not in _INLINABLE_PHOTO_SUBTYPES:
        return text
    line = f"PHOTO:data:image/{subtype};base64,{base64.b64encode(data).decode()}"
    return unfolded[: match.start()] + line + unfolded[match.end() :]


class AddressBookChoiceSerializer(serializers.Serializer):
    id = RemotePathField()
    target = serializers.CharField(max_length=64, default=SCOPE_MINE)

    def validate_id(self, value):
        if value == "/":
            raise serializers.ValidationError("Pick an address book.")
        return value

    def validate_target(self, value):
        # Kept as the wire string: options are stored as JSON, and the scope
        # is resolved again at every slice.
        parse_scope(self.context["owner"], value)
        return value


class ContactsImportOptionsSerializer(serializers.Serializer):
    books = serializers.ListField(
        child=AddressBookChoiceSerializer(), allow_empty=False, max_length=100
    )

    def validate_books(self, value):
        ids = [book["id"] for book in value]
        if len(set(ids)) != len(ids):
            raise serializers.ValidationError("An address book is picked twice.")
        return [dict(book) for book in value]


class _ErrorStreak:
    """Consecutive remote failures; too many and the remote is taken for down."""

    def __init__(self):
        self.count = 0

    def bump(self):
        self.count += 1
        if self.count >= settings.IMPORTS_MAX_CONSECUTIVE_ERRORS:
            raise JobFailed(
                f"Stopped after {self.count} consecutive errors - "
                "the remote server looks unavailable."
            )

    def reset(self):
        self.count = 0


class ContactsImporter(Importer):
    kind = KIND_CONTACTS
    option_serializer = ContactsImportOptionsSerializer

    def run(self, ctx):
        # Resolved before the remote is contacted: a group the user has left
        # stops the job before anything is written.
        scopes = {
            book["id"]: self._scope(ctx, book["target"])
            for book in ctx.options["books"]
        }
        source = ctx.provider.contact_source(ctx.connection)
        try:
            if not ctx.stats.get("planned"):
                ctx.set_phase("listing")
                outcome = self._plan(ctx, source)
                if outcome is not None:
                    return outcome
            ctx.set_phase("importing")
            outcome = self._import(ctx, source, scopes)
            if outcome is Outcome.DONE:
                ctx.set_phase("done")
            return outcome
        finally:
            source.close()

    def live_targets(self, owner, target_uuids):
        alive = set()
        for chunk in batched(target_uuids, 500, strict=False):
            alive.update(
                user_persons(owner)
                .filter(uuid__in=chunk)
                .values_list("uuid", flat=True)
            )
        return alive

    def summarize(self, stats):
        parts = []
        for key, label in (
            ("created", "contacts added"),
            ("updated", "updated"),
            ("unchanged", "unchanged"),
            ("lists", "lists"),
            ("failed", "failed"),
        ):
            if stats.get(key):
                parts.append(f"{stats[key]} {label}")
        return ", ".join(parts) or "Nothing to import."

    def _scope(self, ctx, target):
        try:
            return parse_scope(ctx.owner, target)
        except serializers.ValidationError as exc:
            raise JobFailed(
                "An address book was to be imported into a group you are no "
                "longer a member of."
            ) from exc

    def _book_ids(self, ctx):
        return [book["id"] for book in ctx.options["books"]]

    # -- listing phase -------------------------------------------------

    def _plan(self, ctx, source):
        """Count the cards so the UI has a total; the ones already imported
        with the same etag count as unchanged."""
        queue = ctx.stats.setdefault("book_plan_queue", self._book_ids(ctx))
        ctx.stats.setdefault("total_cards", 0)
        ctx.stats.setdefault("unchanged", 0)
        while queue:
            if stop := ctx.should_stop():
                ctx.flush(force=True)
                return stop
            book_id = queue[0]
            # Applied once the book is listed: a slice cut in the middle
            # re-lists it, and half-applied counts would be added twice.
            cards = unchanged = 0
            try:
                for card_id, etag in source.card_refs(book_id):
                    cards += 1
                    if ctx.already_done(card_id, etag):
                        unchanged += 1
            except ProviderError as exc:
                raise JobFailed(
                    f"Could not list the address book '{book_id}': {exc.user_message}"
                ) from exc
            ctx.stat("total_cards", cards)
            ctx.stat("unchanged", unchanged)
            queue.pop(0)
            ctx.flush()
        ctx.stats["planned"] = True
        ctx.stats.pop("book_plan_queue", None)
        ctx.flush(force=True)
        return None

    # -- importing phase -----------------------------------------------

    def _import(self, ctx, source, scopes):
        queue = ctx.stats.setdefault("book_queue", self._book_ids(ctx))
        errors = _ErrorStreak()
        while queue:
            book_id = queue[0]
            outcome = self._import_book(ctx, source, book_id, scopes[book_id], errors)
            if outcome is not Outcome.DONE:
                ctx.flush(force=True)
                return outcome
            queue.pop(0)
            ctx.stat("books")
            ctx.flush(force=True)
        ctx.current = ""
        ctx.stats.pop("book_queue", None)
        ctx.flush(force=True)
        return Outcome.DONE

    def _import_book(self, ctx, source, book_id, scope, errors):
        """Import the cards of one book not imported yet. Group cards wait for
        the end of the book, so the members they name exist by then."""
        # A slice resuming mid-book must not recount a card this job already
        # failed on - Retry starts a new job, so that card is still retried.
        failed_here = set(
            ImportJobItem.objects.filter(
                job=ctx.job, kind=self.kind, status=ImportJobItem.Status.FAILED
            ).values_list("remote_id", flat=True)
        )
        try:
            etags = {
                card_id: etag
                for card_id, etag in source.card_refs(book_id)
                if not ctx.already_done(card_id, etag) and card_id not in failed_here
            }
        except ProviderError as exc:
            ctx.report_item(
                book_id, ImportJobItem.Status.FAILED, error=exc.user_message
            )
            ctx.stat("failed")
            errors.bump()
            return Outcome.DONE
        groups = []
        for batch in batched(etags, FETCH_BATCH, strict=False):
            if stop := ctx.should_stop():
                return stop
            try:
                cards = list(source.fetch_cards(book_id, list(batch)))
            except ProviderError as exc:
                for card_id in batch:
                    self._fail(ctx, card_id, exc.user_message, etags[card_id])
                errors.bump()
                continue
            errors.reset()
            requested = set(batch)
            fetched = set()
            for card in cards:
                if card.id not in requested or card.id in fetched:
                    continue
                fetched.add(card.id)
                if is_group_card(card.text):
                    groups.append(card)
                    continue
                if stop := ctx.should_stop():
                    return stop
                self._import_card(ctx, source, card, scope, card.etag or etags[card.id])
            for card_id in batch:
                if card_id not in fetched:
                    self._fail(
                        ctx,
                        card_id,
                        "The contact disappeared from the server during the import.",
                        etags[card_id],
                    )
        for card in groups:
            if stop := ctx.should_stop():
                return stop
            self._import_card(ctx, source, card, scope, card.etag or etags[card.id])
        return Outcome.DONE

    def _import_card(self, ctx, source, card, scope, etag):
        ctx.current = card.id
        try:
            text = inline_linked_photo(card.text, source.fetch_photo)
        except Exception as exc:
            logger.warning(
                "Photo fetch for contact %s failed: %s",
                scrub(card.id[:200]),
                scrub(str(exc)),
            )
            text = card.text
        try:
            # The contact and its DONE record commit together: a worker dying
            # between the two would leave a contact the next run cannot place.
            with transaction.atomic():
                report = import_vcards(text, **scope)
                new_lists = sum(
                    file_in_category_lists(person, scope) for person in report.persons
                )
                # A group card stores a list, not a person: recorded without a
                # target, it is imported again on every run, which is harmless.
                target = report.persons[0].uuid if report.persons else None
                ctx.report_item(
                    card.id,
                    ImportJobItem.Status.DONE,
                    target_uuid=target,
                    fingerprint=etag,
                )
        except (VCardError, DataError, IntegrityError) as exc:
            logger.warning(
                "Import of contact %s failed: %s",
                scrub(card.id[:200]),
                scrub(str(exc)),
            )
            self._fail(ctx, card.id, _card_message(exc), etag)
            return
        ctx.stat("cards")
        ctx.stat("created", report.created)
        ctx.stat("updated", report.updated)
        ctx.stat("lists", report.lists + new_lists)

    def _fail(self, ctx, card_id, message, etag):
        ctx.report_item(
            card_id, ImportJobItem.Status.FAILED, error=message, fingerprint=etag
        )
        ctx.stat("failed")


def _card_message(exc):
    if isinstance(exc, VCardError):
        return "This contact is not a valid vCard."
    return "Could not store the contact."
