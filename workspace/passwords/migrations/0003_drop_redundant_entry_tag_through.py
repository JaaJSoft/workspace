from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('passwords', '0002_drop_redundant_fk_indexes'),
    ]

    operations = [
        # M2M fields with through= cannot be altered in place; drop and re-add.
        migrations.RemoveField(
            model_name='passwordentry',
            name='tags',
        ),
        migrations.RemoveConstraint(
            model_name='passwordentrytag',
            name='entry_tag_uniq',
        ),
        migrations.DeleteModel(
            name='PasswordEntryTag',
        ),
        migrations.AddField(
            model_name='passwordentry',
            name='tags',
            field=models.ManyToManyField(
                blank=True,
                related_name='entries',
                to='passwords.passwordtag',
            ),
        ),
    ]
