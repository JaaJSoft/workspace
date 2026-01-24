from django.db import migrations, models


def backfill_paths(apps, schema_editor):
    File = apps.get_model('files', 'File')
    nodes = list(File.objects.values_list('uuid', 'parent_id', 'name'))
    node_map = {
        node_id: (parent_id, name)
        for node_id, parent_id, name in nodes
    }
    path_map = {}

    def resolve(node_id, visiting):
        if node_id in path_map:
            return path_map[node_id]
        if node_id in visiting:
            return None
        visiting.add(node_id)
        try:
            parent_id, name = node_map.get(node_id, (None, None))
            if name is None:
                return None
            if parent_id:
                parent_path = resolve(parent_id, visiting)
                path = f"{parent_path}/{name}" if parent_path else name
            else:
                path = name
            path_map[node_id] = path
            return path
        finally:
            visiting.remove(node_id)

    for node_id in node_map:
        resolve(node_id, set())

    if not path_map:
        return

    objs = [
        File(uuid=node_id, path=path)
        for node_id, path in path_map.items()
        if path
    ]
    if objs:
        File.objects.bulk_update(objs, ['path'])


class Migration(migrations.Migration):

    dependencies = [
        ('files', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='file',
            name='path',
            field=models.TextField(
                blank=True,
                editable=False,
                help_text='Full path from root to this node.',
                null=True,
            ),
        ),
        migrations.RunPython(backfill_paths, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='file',
            name='path',
            field=models.TextField(
                blank=True,
                editable=False,
                help_text='Full path from root to this node.',
            ),
        ),
    ]
