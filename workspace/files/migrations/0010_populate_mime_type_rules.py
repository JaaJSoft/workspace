"""Populate MimeTypeRule with initial data consolidating all MIME type sources."""

from django.db import migrations

# (pattern, priority, icon, color, category, viewer_type)
RULES = [
    # --- Exact text / code types  (priority 10) ---
    ("text/plain", 10, "file-text", "text-info", "text", "text"),
    ("text/markdown", 10, "file-text", "text-info", "text", "markdown"),
    ("text/csv", 10, "file-text", "text-info", "text", "text"),
    ("text/html", 10, "file-code", "text-info", "text", "text"),
    ("text/css", 10, "file-code", "text-info", "text", "text"),
    ("text/x-python", 10, "file-code", "text-info", "text", "text"),
    ("text/x-java", 10, "file-code", "text-info", "text", "text"),
    ("text/x-c", 10, "file-code", "text-info", "text", "text"),
    ("text/x-c++", 10, "file-code", "text-info", "text", "text"),
    ("text/x-sh", 10, "file-code", "text-info", "text", "text"),
    ("text/x-script.python", 10, "file-code", "text-info", "text", "text"),

    # --- Application text-like types (priority 10) ---
    ("application/json", 10, "file-json", "text-info", "text", "text"),
    ("application/xml", 10, "file-code", "text-info", "text", "text"),
    ("application/javascript", 10, "file-code", "text-info", "text", "text"),
    ("application/x-python-code", 10, "file-code", "text-info", "text", "text"),

    # --- Image types (priority 10) ---
    ("image/jpeg", 10, "image", "text-success", "image", "image"),
    ("image/png", 10, "image", "text-success", "image", "image"),
    ("image/gif", 10, "image", "text-success", "image", "image"),
    ("image/webp", 10, "image", "text-success", "image", "image"),
    ("image/svg+xml", 10, "image", "text-success", "image", "image"),
    ("image/bmp", 10, "image", "text-success", "image", "image"),
    ("image/tiff", 10, "image", "text-success", "image", "image"),
    ("image/x-icon", 10, "image", "text-success", "image", "image"),  # fix: was missing viewer

    # --- PDF (priority 10) ---
    ("application/pdf", 10, "file-text", "text-error", "pdf", "pdf"),

    # --- Video types (priority 10) ---
    ("video/mp4", 10, "video", "text-error", "video", "media"),
    ("video/webm", 10, "video", "text-error", "video", "media"),
    ("video/ogg", 10, "video", "text-error", "video", "media"),
    ("video/quicktime", 10, "video", "text-error", "video", "media"),
    ("video/x-msvideo", 10, "video", "text-error", "video", "media"),
    ("video/x-matroska", 10, "video", "text-error", "video", "media"),

    # --- Audio types (priority 10) ---
    ("audio/mpeg", 10, "music", "text-secondary", "audio", "media"),
    ("audio/wav", 10, "music", "text-secondary", "audio", "media"),
    ("audio/ogg", 10, "music", "text-secondary", "audio", "media"),
    ("audio/webm", 10, "music", "text-secondary", "audio", "media"),
    ("audio/aac", 10, "music", "text-secondary", "audio", "media"),
    ("audio/mp4", 10, "music", "text-secondary", "audio", "media"),
    ("audio/x-m4a", 10, "music", "text-secondary", "audio", "media"),

    # --- Archives (priority 10, no viewer) ---
    ("application/zip", 10, "file-archive", "text-warning", "unknown", None),
    ("application/x-tar", 10, "file-archive", "text-warning", "unknown", None),
    ("application/gzip", 10, "file-archive", "text-warning", "unknown", None),
    ("application/x-rar-compressed", 10, "file-archive", "text-warning", "unknown", None),

    # --- Office docs (priority 10, no viewer) ---
    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 10, "file-spreadsheet", "text-base-content/60", "unknown", None),
    ("application/vnd.ms-excel", 10, "file-spreadsheet", "text-base-content/60", "unknown", None),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", 10, "file-text", "text-base-content/60", "unknown", None),
    ("application/msword", 10, "file-text", "text-base-content/60", "unknown", None),
    ("application/vnd.openxmlformats-officedocument.presentationml.presentation", 10, "file-presentation", "text-base-content/60", "unknown", None),
    ("application/vnd.ms-powerpoint", 10, "file-presentation", "text-base-content/60", "unknown", None),

    # --- Wildcards (priority 1000) ---
    ("text/*", 1000, "file-text", "text-info", "text", "text"),
    ("image/*", 1000, "image", "text-success", "image", "image"),
    ("video/*", 1000, "video", "text-error", "video", "media"),
    ("audio/*", 1000, "music", "text-secondary", "audio", "media"),
]


def populate(apps, schema_editor):
    MimeTypeRule = apps.get_model("files", "MimeTypeRule")
    objs = []
    for pattern, priority, icon, color, category, viewer_type in RULES:
        objs.append(MimeTypeRule(
            pattern=pattern,
            is_wildcard=pattern.endswith("/*"),
            priority=priority,
            icon=icon,
            color=color,
            category=category,
            viewer_type=viewer_type or "",
        ))
    MimeTypeRule.objects.bulk_create(objs)


def depopulate(apps, schema_editor):
    MimeTypeRule = apps.get_model("files", "MimeTypeRule")
    MimeTypeRule.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("files", "0009_mimetyperule"),
    ]

    operations = [
        migrations.RunPython(populate, depopulate),
    ]
