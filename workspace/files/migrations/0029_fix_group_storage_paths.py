"""Fix duplicated group name in storage paths.

Group files were stored under ``files/groups/<group_name>/<group_root_folder>/...``
which duplicated the group name because the root folder is always named after the
group.  The correct layout (symmetric with users) is ``files/groups/<path>`` where
``<path>`` already starts with the group root folder name.

This migration:
1. Moves physical directories on disk from the old nested layout to the flat one.
2. Rewrites ``content.name`` in the database for affected file rows.
"""

from django.db import migrations


def fix_db_content_names(apps, schema_editor):
    """Rewrite content.name: files/groups/<grp>/<root>/... -> files/groups/<root>/..."""
    import re

    db = schema_editor.connection.alias
    File = apps.get_model('files', 'File')
    files = (
        File.objects.using(db)
        .filter(node_type='file', group__isnull=False)
        .exclude(content='')
        .exclude(content__isnull=True)
    )

    # Extract the group segment from the stored path itself rather than
    # f.group.name, because the group may have been renamed since upload.
    dup_re = re.compile(r'^files/groups/([^/]+)/\1(/.*)?$')

    updated = []
    for f in files.iterator(chunk_size=500):
        if not f.content.name:
            continue
        name = f.content.name.replace('\\', '/')
        m = dup_re.match(name)
        if m:
            # Strip the duplicated first segment:
            # files/groups/X/X/rest -> files/groups/X/rest
            f.content.name = 'files/groups/' + name[len(f'files/groups/{m.group(1)}/'):]
            updated.append(f)
        if len(updated) >= 500:
            File.objects.using(db).bulk_update(updated, ['content'], batch_size=500)
            updated = []
    if updated:
        File.objects.using(db).bulk_update(updated, ['content'], batch_size=500)


def move_physical_dirs(apps, schema_editor):
    """Move files/groups/<grp>/<contents> up one level so <grp> IS the root dir."""
    import os
    from django.core.files.storage import default_storage

    try:
        groups_root = default_storage.path('files/groups')
    except NotImplementedError:
        return

    if not os.path.isdir(groups_root):
        return

    for group_dir_name in os.listdir(groups_root):
        group_dir = os.path.join(groups_root, group_dir_name)
        if not os.path.isdir(group_dir):
            continue

        # Only operate on directories that have the legacy duplicated
        # layout: a nested subdirectory with the same name as the parent.
        # Already-flat directories (post-fix files) are left alone.
        nested = os.path.join(group_dir, group_dir_name)
        if not os.path.isdir(nested):
            continue

        # Move each child of the old <group_name>/ dir up into groups/
        for child in os.listdir(group_dir):
            src = os.path.join(group_dir, child)
            dest = os.path.join(groups_root, child)
            if os.path.exists(dest):
                # Merge: move contents of src into existing dest
                if os.path.isdir(src) and os.path.isdir(dest):
                    for item in os.listdir(src):
                        item_src = os.path.join(src, item)
                        item_dest = os.path.join(dest, item)
                        if not os.path.exists(item_dest):
                            os.rename(item_src, item_dest)
                    try:
                        os.rmdir(src)
                    except OSError:
                        pass  # dir may not be empty due to concurrent writes
            else:
                os.rename(src, dest)

        # Best-effort cleanup: the namespace dir should be empty now.
        try:
            os.rmdir(group_dir)
        except OSError:
            pass  # dir may still contain files if moves were partial


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0028_update_file_event_choice_labels'),
    ]

    operations = [
        migrations.RunPython(move_physical_dirs, migrations.RunPython.noop),
        migrations.RunPython(fix_db_content_names, migrations.RunPython.noop),
    ]
