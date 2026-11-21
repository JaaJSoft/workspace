"""The tags every library is tagged with, whatever tags its owner made.

Each is an Open Images label, so the embedding bench (scripts/embedding_bench.py)
measures it on photos people verified, and calibrates the threshold it is
placed under (``EmbeddingBackend.tag_max_distance``). The tag is created
with its icon, under its name in its owner's language, and a photo matched
against the English name in lower case: the vectors are of English text.

Plain data, importing nothing: the bench loads this file by its path, outside
Django.
"""

from __future__ import annotations

from typing import NamedTuple


def gettext_noop(message):
    """Django's marker, defined here: makemessages extracts the names it
    wraps whatever it is bound to, and auto_tags translates them."""
    return message


class ClassicTag(NamedTuple):
    # The Open Images display name the bench scores it against.
    label: str
    # In English; auto_tags translates it for the tag it creates.
    name: str
    # The Lucide icon the tag is created with; its owner can change it.
    icon: str = "tag"

    @property
    def text(self):
        """What a photo's vector is compared to, in English whatever the
        owner's language."""
        return self.name.lower()


CLASSIC_TAGS = (
    ClassicTag("Dog", gettext_noop("Dog"), "dog"),
    ClassicTag("Cat", gettext_noop("Cat"), "cat"),
    ClassicTag("Bird", gettext_noop("Bird"), "bird"),
    ClassicTag("Horse", gettext_noop("Horse"), "paw-print"),
    ClassicTag("Beach", gettext_noop("Beach"), "tree-palm"),
    ClassicTag("Mountain", gettext_noop("Mountain"), "mountain"),
    ClassicTag("Snow", gettext_noop("Snow"), "snowflake"),
    ClassicTag("Sea", gettext_noop("Sea"), "waves"),
    ClassicTag("Forest", gettext_noop("Forest"), "trees"),
    ClassicTag("Lake", gettext_noop("Lake"), "fish"),
    ClassicTag("Sunset", gettext_noop("Sunset"), "sunset"),
    ClassicTag("Flower", gettext_noop("Flowers"), "flower-2"),
    ClassicTag("Garden", gettext_noop("Garden"), "sprout"),
    ClassicTag("City", gettext_noop("City"), "building-2"),
    ClassicTag("Night", gettext_noop("Night"), "moon-star"),
    ClassicTag("Car", gettext_noop("Car"), "car"),
    ClassicTag("Boat", gettext_noop("Boat"), "sailboat"),
    ClassicTag("Airplane", gettext_noop("Airplane"), "plane"),
    ClassicTag("Bicycle", gettext_noop("Bicycle"), "bike"),
    ClassicTag("Meal", gettext_noop("Meal"), "utensils"),
    ClassicTag("Cake", gettext_noop("Cake"), "cake-slice"),
    ClassicTag("Cocktail", gettext_noop("Cocktail"), "martini"),
    ClassicTag("Baby", gettext_noop("Baby"), "baby"),
    ClassicTag("Child", gettext_noop("Child"), "toy-brick"),
    ClassicTag("Selfie", gettext_noop("Selfie"), "scan-face"),
    ClassicTag("Wedding", gettext_noop("Wedding"), "gem"),
    ClassicTag("Birthday", gettext_noop("Birthday"), "cake"),
    ClassicTag("Party", gettext_noop("Party"), "party-popper"),
    ClassicTag("Christmas", gettext_noop("Christmas"), "tree-pine"),
    ClassicTag("Skiing", gettext_noop("Skiing"), "cable-car"),
    ClassicTag("Swimming pool", gettext_noop("Swimming pool"), "waves-ladder"),
    ClassicTag("Screenshot", gettext_noop("Screenshot"), "monitor-smartphone"),
    ClassicTag("Document", gettext_noop("Document"), "file-text"),
)
