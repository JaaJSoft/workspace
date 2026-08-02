from django.db import migrations

# Exact machine-generated prefix that render_tool_badges() (removed) appended
# to body_html; common to both the inline and the one-per-line variants.
BADGE_MARKER = '\n<div class="mt-2 text-xs text-base-content/40 flex '

BATCH_SIZE = 500


def _strip_badge_block(body_html):
    idx = body_html.find(BADGE_MARKER)
    if idx == -1:
        return body_html
    return body_html[:idx]


def strip_tool_badges(apps, schema_editor):
    """Remove legacy inline tool badges; the UI now renders from tool_data.

    Only messages whose tool_data is a list (AI rounds) are touched: call
    messages store a dict there, and pre-tool_data messages keep their badges
    since there is no other record of the tools used.
    """
    Message = apps.get_model("chat", "Message")
    qs = Message.objects.filter(
        tool_data__isnull=False, body_html__contains=BADGE_MARKER
    ).only("uuid", "body_html", "tool_data")
    batch = []
    for msg in qs.iterator():
        if not isinstance(msg.tool_data, list):
            continue
        msg.body_html = _strip_badge_block(msg.body_html)
        batch.append(msg)
        if len(batch) >= BATCH_SIZE:
            Message.objects.bulk_update(batch, ["body_html"])
            batch = []
    if batch:
        Message.objects.bulk_update(batch, ["body_html"])


class Migration(migrations.Migration):
    dependencies = [
        ("chat", "0022_conversation_groups"),
    ]

    operations = [
        migrations.RunPython(strip_tool_badges, migrations.RunPython.noop),
    ]
