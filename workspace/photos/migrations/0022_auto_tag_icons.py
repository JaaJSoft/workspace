from django.db import migrations

# The classic tags' icons as of this migration (tag_vocabulary.CLASSIC_TAGS),
# inlined: a migration must not follow the live vocabulary.
_ICONS = {
    "dog": "dog",
    "cat": "cat",
    "bird": "bird",
    "horse": "paw-print",
    "beach": "tree-palm",
    "mountain": "mountain",
    "snow": "snowflake",
    "sea": "waves",
    "forest": "trees",
    "lake": "fish",
    "sunset": "sunset",
    "flowers": "flower-2",
    "garden": "sprout",
    "city": "building-2",
    "night": "moon-star",
    "car": "car",
    "boat": "sailboat",
    "airplane": "plane",
    "bicycle": "bike",
    "meal": "utensils",
    "cake": "cake-slice",
    "cocktail": "martini",
    "baby": "baby",
    "child": "toy-brick",
    "selfie": "scan-face",
    "wedding": "gem",
    "birthday": "cake",
    "party": "party-popper",
    "christmas": "tree-pine",
    "skiing": "cable-car",
    "swimming pool": "waves-ladder",
    "screenshot": "monitor-smartphone",
    "document": "file-text",
}


def give_auto_tags_their_icon(apps, schema_editor):
    """The tags automatic tagging created before it set an icon: a tag
    carrying an automatic assignment, still without an icon of its own."""
    Tag = apps.get_model("files", "Tag")
    FileTag = apps.get_model("files", "FileTag")
    db = schema_editor.connection.alias
    auto_tag_ids = FileTag.objects.using(db).filter(source="auto").values("tag_id")
    tags = Tag.objects.using(db).filter(icon="", uuid__in=auto_tag_ids)
    batch = []
    for tag in tags.iterator():
        icon = _ICONS.get(tag.name.lower())
        if icon:
            tag.icon = icon
            batch.append(tag)
    Tag.objects.using(db).bulk_update(batch, ["icon"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("photos", "0021_revoke_lapsed_album_sharing"),
        ("files", "0071_upload_session"),
    ]

    operations = [
        migrations.RunPython(give_auto_tags_their_icon, migrations.RunPython.noop),
    ]
