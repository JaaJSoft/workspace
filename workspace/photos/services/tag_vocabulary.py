"""The tags every library is tagged with, whatever tags its owner made.

Each is an Open Images label, so the embedding bench (scripts/embedding_bench.py)
measures it on photos people verified, and calibrates the threshold it is
placed under (``EmbeddingBackend.tag_max_distance``). The tag is created
under its name, and a photo matched against that name in lower case.

Plain data, importing nothing: the bench loads this file by its path, outside
Django.
"""

from __future__ import annotations

from typing import NamedTuple


class ClassicTag(NamedTuple):
    # The Open Images display name the bench scores it against.
    label: str
    name: str

    @property
    def text(self):
        """What a photo's vector is compared to."""
        return self.name.lower()


CLASSIC_TAGS = (
    ClassicTag("Dog", "Dog"),
    ClassicTag("Cat", "Cat"),
    ClassicTag("Bird", "Bird"),
    ClassicTag("Horse", "Horse"),
    ClassicTag("Beach", "Beach"),
    ClassicTag("Mountain", "Mountain"),
    ClassicTag("Snow", "Snow"),
    ClassicTag("Sea", "Sea"),
    ClassicTag("Forest", "Forest"),
    ClassicTag("Lake", "Lake"),
    ClassicTag("Sunset", "Sunset"),
    ClassicTag("Flower", "Flowers"),
    ClassicTag("Garden", "Garden"),
    ClassicTag("City", "City"),
    ClassicTag("Night", "Night"),
    ClassicTag("Car", "Car"),
    ClassicTag("Boat", "Boat"),
    ClassicTag("Airplane", "Airplane"),
    ClassicTag("Bicycle", "Bicycle"),
    ClassicTag("Meal", "Meal"),
    ClassicTag("Cake", "Cake"),
    ClassicTag("Cocktail", "Cocktail"),
    ClassicTag("Baby", "Baby"),
    ClassicTag("Child", "Child"),
    ClassicTag("Selfie", "Selfie"),
    ClassicTag("Wedding", "Wedding"),
    ClassicTag("Birthday", "Birthday"),
    ClassicTag("Party", "Party"),
    ClassicTag("Christmas", "Christmas"),
    ClassicTag("Skiing", "Skiing"),
    ClassicTag("Swimming pool", "Swimming pool"),
    ClassicTag("Screenshot", "Screenshot"),
    ClassicTag("Document", "Document"),
)
