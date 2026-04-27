import django.db.models.deletion
import workspace.common.uuids
import workspace.passwords.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Vault',
            fields=[
                ('uuid', models.UUIDField(default=workspace.common.uuids.uuid_v7_or_v4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('name', models.CharField(default='Personal', max_length=100)),
                ('description', models.TextField(blank=True, default='')),
                ('icon', models.CharField(blank=True, default='vault', max_length=50)),
                ('color', models.CharField(blank=True, default='text-warning', max_length=50)),
                ('master_password_hash', models.CharField(blank=True, default='', max_length=255)),
                ('protected_vault_key', models.TextField(blank=True, default='')),
                ('kdf_algorithm', models.CharField(choices=[('pbkdf2_sha256', 'PBKDF2-SHA256'), ('argon2id', 'Argon2id')], default='pbkdf2_sha256', max_length=20)),
                ('kdf_iterations', models.PositiveIntegerField(default=600000)),
                ('kdf_salt', models.CharField(blank=True, default='', max_length=44)),
                ('kdf_memory', models.PositiveIntegerField(blank=True, null=True)),
                ('kdf_parallelism', models.PositiveIntegerField(blank=True, null=True)),
                ('is_setup', models.BooleanField(default=False)),
                ('is_favorite', models.BooleanField(db_index=True, default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='vaults', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='vault',
            index=models.Index(fields=['user'], name='vault_user_idx'),
        ),
        migrations.CreateModel(
            name='PasswordFolder',
            fields=[
                ('uuid', models.UUIDField(default=workspace.common.uuids.uuid_v7_or_v4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('icon', models.CharField(blank=True, default='folder', max_length=50)),
                ('color', models.CharField(blank=True, default='', max_length=50)),
                ('order', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('vault', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='folders', to='passwords.vault')),
                ('parent', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='children', to='passwords.passwordfolder')),
            ],
            options={
                'ordering': ['order', 'name'],
            },
        ),
        migrations.AddIndex(
            model_name='passwordfolder',
            index=models.Index(fields=['vault'], name='folder_vault_idx'),
        ),
        migrations.AddIndex(
            model_name='passwordfolder',
            index=models.Index(fields=['parent'], name='folder_parent_idx'),
        ),
        migrations.CreateModel(
            name='PasswordTag',
            fields=[
                ('uuid', models.UUIDField(default=workspace.common.uuids.uuid_v7_or_v4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('name', models.CharField(max_length=100)),
                ('color', models.CharField(blank=True, default='', max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('vault', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tags', to='passwords.vault')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.AddIndex(
            model_name='passwordtag',
            index=models.Index(fields=['vault'], name='tag_vault_idx'),
        ),
        migrations.AddConstraint(
            model_name='passwordtag',
            constraint=models.UniqueConstraint(fields=('vault', 'name'), name='tag_vault_name_uniq'),
        ),
        migrations.CreateModel(
            name='PasswordEntry',
            fields=[
                ('uuid', models.UUIDField(default=workspace.common.uuids.uuid_v7_or_v4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('type', models.CharField(choices=[('login', 'Login')], db_index=True, default='login', max_length=20)),
                ('encrypted_name', models.TextField()),
                ('icon', models.CharField(blank=True, default='key-round', max_length=50)),
                ('icon_color', models.CharField(blank=True, default='', max_length=50)),
                ('custom_icon', models.ImageField(blank=True, null=True, upload_to=workspace.passwords.models.entry_icon_upload_path)),
                ('is_favorite', models.BooleanField(db_index=True, default=False)),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('last_used_at', models.DateTimeField(blank=True, db_index=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('vault', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entries', to='passwords.vault')),
                ('folder', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='entries', to='passwords.passwordfolder')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='passwordentry',
            index=models.Index(fields=['vault', 'type'], name='entry_vault_type_idx'),
        ),
        migrations.AddIndex(
            model_name='passwordentry',
            index=models.Index(fields=['vault', 'deleted_at'], name='entry_vault_deleted_idx'),
        ),
        migrations.AddIndex(
            model_name='passwordentry',
            index=models.Index(fields=['vault', 'is_favorite'], name='entry_vault_fav_idx'),
        ),
        migrations.CreateModel(
            name='PasswordEntryTag',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('entry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='passwords.passwordentry')),
                ('tag', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='passwords.passwordtag')),
            ],
        ),
        migrations.AddConstraint(
            model_name='passwordentrytag',
            constraint=models.UniqueConstraint(fields=('entry', 'tag'), name='entry_tag_uniq'),
        ),
        migrations.AddField(
            model_name='passwordentry',
            name='tags',
            field=models.ManyToManyField(blank=True, related_name='entries', through='passwords.PasswordEntryTag', to='passwords.passwordtag'),
        ),
        migrations.CreateModel(
            name='LoginEntry',
            fields=[
                ('passwordentry_ptr', models.OneToOneField(auto_created=True, on_delete=django.db.models.deletion.CASCADE, parent_link=True, primary_key=True, serialize=False, to='passwords.passwordentry')),
                ('encrypted_username', models.TextField(blank=True, default='')),
                ('encrypted_password', models.TextField(blank=True, default='')),
                ('encrypted_totp_secret', models.TextField(blank=True, default='')),
                ('uris', models.JSONField(blank=True, default=list)),
                ('notes', models.TextField(blank=True, default='')),
            ],
            options={
                'verbose_name': 'Login entry',
                'verbose_name_plural': 'Login entries',
            },
            bases=('passwords.passwordentry',),
        ),
        migrations.CreateModel(
            name='UserKeyPair',
            fields=[
                ('uuid', models.UUIDField(default=workspace.common.uuids.uuid_v7_or_v4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('algorithm', models.CharField(default='ecdh_p256', max_length=20)),
                ('public_key', models.TextField()),
                ('protected_private_key', models.TextField()),
                ('kdf_salt', models.CharField(max_length=44)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='key_pair', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'User key pair',
            },
        ),
        migrations.CreateModel(
            name='VaultMember',
            fields=[
                ('uuid', models.UUIDField(default=workspace.common.uuids.uuid_v7_or_v4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('role', models.CharField(choices=[('viewer', 'Viewer'), ('editor', 'Editor'), ('manager', 'Manager')], default='viewer', max_length=20)),
                ('status', models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('revoked', 'Revoked')], default='pending', max_length=20)),
                ('protected_vault_key', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('vault', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='passwords.vault')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='vault_memberships', to=settings.AUTH_USER_MODEL)),
                ('invited_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='vault_invitations_sent', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='vaultmember',
            constraint=models.UniqueConstraint(fields=('vault', 'user'), name='vault_member_uniq'),
        ),
        migrations.AddIndex(
            model_name='vaultmember',
            index=models.Index(fields=['vault', 'status'], name='vault_member_vault_status_idx'),
        ),
        migrations.AddIndex(
            model_name='vaultmember',
            index=models.Index(fields=['user', 'status'], name='vault_member_user_status_idx'),
        ),
        migrations.CreateModel(
            name='VaultGroupAccess',
            fields=[
                ('uuid', models.UUIDField(default=workspace.common.uuids.uuid_v7_or_v4, editable=False, primary_key=True, serialize=False, unique=True)),
                ('role', models.CharField(choices=[('viewer', 'Viewer'), ('editor', 'Editor'), ('manager', 'Manager')], default='viewer', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('vault', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='group_accesses', to='passwords.vault')),
                ('group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='vault_accesses', to='auth.group')),
                ('granted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='vault_group_grants', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='vaultgroupaccess',
            constraint=models.UniqueConstraint(fields=('vault', 'group'), name='vault_group_uniq'),
        ),
        migrations.AddIndex(
            model_name='vaultgroupaccess',
            index=models.Index(fields=['vault'], name='vault_group_vault_idx'),
        ),
    ]
