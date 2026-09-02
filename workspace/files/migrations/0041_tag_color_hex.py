from django.db import migrations, models

# Tag colors used to be daisyUI tokens rendered as `badge-<token>`, which
# meant the picker's `pink-500` and `orange-500` produced classes that do
# not exist in daisyUI and rendered grey. They are now CSS colors, from the
# same palette as projects labels, so a tag renders the color it was given.
TOKEN_TO_HEX = {
    "error": "#ef4444",
    "orange-500": "#f97316",
    "warning": "#eab308",
    "success": "#22c55e",
    "info": "#3b82f6",
    "primary": "#3b82f6",
    "secondary": "#a855f7",
    "pink-500": "#ec4899",
    "accent": "#06b6d4",
    # 'ghost' and 'neutral' were the "no color" values.
    "ghost": "",
    "neutral": "",
}


def tokens_to_hex(apps, schema_editor):
    Tag = apps.get_model("files", "Tag")
    db = schema_editor.connection.alias
    for token, hex_color in TOKEN_TO_HEX.items():
        Tag.objects.using(db).filter(color=token).update(color=hex_color)
    # Anything else (hand-written API values) has no meaning as a CSS
    # color; the neutral chip beats an invalid style declaration.
    Tag.objects.using(db).exclude(color__startswith="#").exclude(color="").update(color="")


def hex_to_tokens(apps, schema_editor):
    Tag = apps.get_model("files", "Tag")
    db = schema_editor.connection.alias
    reverse = {v: k for k, v in TOKEN_TO_HEX.items() if v}
    for hex_color, token in reverse.items():
        Tag.objects.using(db).filter(color=hex_color).update(color=token)
    Tag.objects.using(db).filter(color="").update(color="ghost")


class Migration(migrations.Migration):
    dependencies = [
        ("files", "0040_file_viewer"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tag",
            name="color",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.RunPython(tokens_to_hex, hex_to_tokens),
    ]
