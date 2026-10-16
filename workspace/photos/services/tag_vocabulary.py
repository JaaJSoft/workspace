"""The tags every library is tagged with, whatever tags its owner made.

Each is an Open Images label, so the embedding bench (scripts/embedding_bench.py)
measures it on photos people verified, and calibrates the threshold it is
placed under (``EmbeddingBackend.tag_max_distance``). The tag is created
under its name and with its icon, and a photo matched against that name in
lower case.

Plain data, importing nothing: the bench loads this file by its path, outside
Django.
"""

from __future__ import annotations

from typing import NamedTuple


class ClassicTag(NamedTuple):
    # The Open Images display name the bench scores it against.
    label: str
    name: str
    # The Lucide icon the tag is created with; its owner can change it.
    icon: str = "tag"

    @property
    def text(self):
        """What a photo's vector is compared to."""
        return self.name.lower()


CLASSIC_TAGS = (
    ClassicTag("Dog", "Dog", "dog"),
    ClassicTag("Cat", "Cat", "cat"),
    ClassicTag("Bird", "Bird", "bird"),
    ClassicTag("Horse", "Horse", "paw-print"),
    ClassicTag("Beach", "Beach", "tree-palm"),
    ClassicTag("Mountain", "Mountain", "mountain"),
    ClassicTag("Snow", "Snow", "snowflake"),
    ClassicTag("Sea", "Sea", "waves"),
    ClassicTag("Forest", "Forest", "trees"),
    ClassicTag("Lake", "Lake", "fish"),
    ClassicTag("Sunset", "Sunset", "sunset"),
    ClassicTag("Flower", "Flowers", "flower-2"),
    ClassicTag("Garden", "Garden", "sprout"),
    ClassicTag("City", "City", "building-2"),
    ClassicTag("Night", "Night", "moon-star"),
    ClassicTag("Car", "Car", "car"),
    ClassicTag("Boat", "Boat", "sailboat"),
    ClassicTag("Airplane", "Airplane", "plane"),
    ClassicTag("Bicycle", "Bicycle", "bike"),
    ClassicTag("Meal", "Meal", "utensils"),
    ClassicTag("Cake", "Cake", "cake-slice"),
    ClassicTag("Cocktail", "Cocktail", "martini"),
    ClassicTag("Baby", "Baby", "baby"),
    ClassicTag("Child", "Child", "toy-brick"),
    ClassicTag("Selfie", "Selfie", "scan-face"),
    ClassicTag("Wedding", "Wedding", "gem"),
    ClassicTag("Birthday", "Birthday", "cake"),
    ClassicTag("Party", "Party", "party-popper"),
    ClassicTag("Christmas", "Christmas", "tree-pine"),
    ClassicTag("Skiing", "Skiing", "cable-car"),
    ClassicTag("Swimming pool", "Swimming pool", "waves-ladder"),
    ClassicTag("Screenshot", "Screenshot", "monitor-smartphone"),
    ClassicTag("Document", "Document", "file-text"),
)
