import django.db.models.deletion
import workspace.common.uuids
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('passwords', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
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
        migrations.AddConstraint(
            model_name='vaultgroupaccess',
            constraint=models.UniqueConstraint(fields=('vault', 'group'), name='vault_group_uniq'),
        ),
        migrations.AddIndex(
            model_name='vaultgroupaccess',
            index=models.Index(fields=['vault'], name='vault_group_vault_idx'),
        ),
    ]
