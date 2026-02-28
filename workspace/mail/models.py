from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4


class MailAccount(models.Model):
    """IMAP/SMTP mail account linked to a user."""

    class AuthMethod(models.TextChoices):
        PASSWORD = 'password', 'Password'
        OAUTH2 = 'oauth2', 'OAuth2'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='mail_accounts',
    )
    email = models.EmailField()
    display_name = models.CharField(max_length=255, blank=True, default='')
    auth_method = models.CharField(
        max_length=10,
        choices=AuthMethod.choices,
        default=AuthMethod.PASSWORD,
    )

    # IMAP settings
    imap_host = models.CharField(max_length=255)
    imap_port = models.PositiveIntegerField(default=993)
    imap_use_ssl = models.BooleanField(default=True)

    # SMTP settings
    smtp_host = models.CharField(max_length=255)
    smtp_port = models.PositiveIntegerField(default=587)
    smtp_use_tls = models.BooleanField(default=True)

    # Credentials
    username = models.CharField(max_length=255)
    password_encrypted = models.BinaryField(null=True, blank=True)

    # OAuth2 (prepared for future use)
    oauth2_provider = models.CharField(max_length=50, blank=True, default='')
    oauth2_data_encrypted = models.BinaryField(null=True, blank=True)

    # IMAP folder hierarchy
    imap_delimiter = models.CharField(max_length=5, default='/')

    # Sync state
    is_active = models.BooleanField(default=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['email']

    def __str__(self):
        return self.email

    def set_password(self, plaintext):
        from workspace.mail.services.credentials import encrypt
        self.password_encrypted = encrypt(plaintext)

    def get_password(self):
        from workspace.mail.services.credentials import decrypt
        if not self.password_encrypted:
            return ''
        return decrypt(bytes(self.password_encrypted))

    def set_oauth2_data(self, data):
        import orjson
        from workspace.mail.services.credentials import encrypt
        self.oauth2_data_encrypted = encrypt(orjson.dumps(data).decode())

    def get_oauth2_data(self):
        import orjson
        from workspace.mail.services.credentials import decrypt
        if not self.oauth2_data_encrypted:
            return None
        return orjson.loads(decrypt(bytes(self.oauth2_data_encrypted)))


class MailFolder(models.Model):
    """IMAP folder synced from a mail account."""

    class FolderType(models.TextChoices):
        INBOX = 'inbox', 'Inbox'
        SENT = 'sent', 'Sent'
        DRAFTS = 'drafts', 'Drafts'
        TRASH = 'trash', 'Trash'
        SPAM = 'spam', 'Spam'
        ARCHIVE = 'archive', 'Archive'
        OTHER = 'other', 'Other'

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    account = models.ForeignKey(
        MailAccount,
        on_delete=models.CASCADE,
        related_name='folders',
    )
    name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)
    folder_type = models.CharField(
        max_length=10,
        choices=FolderType.choices,
        default=FolderType.OTHER,
    )

    icon = models.CharField(max_length=50, null=True, blank=True)
    color = models.CharField(max_length=30, null=True, blank=True)

    uid_validity = models.BigIntegerField(default=0)
    message_count = models.IntegerField(default=0)
    unread_count = models.IntegerField(default=0)
    last_sync_uid = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['account', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['account', 'name'],
                name='unique_mail_folder',
            ),
        ]

    def __str__(self):
        return f'{self.account.email} / {self.display_name}'


class MailMessage(models.Model):
    """Email message synced from IMAP."""

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    account = models.ForeignKey(
        MailAccount,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    folder = models.ForeignKey(
        MailFolder,
        on_delete=models.CASCADE,
        related_name='messages',
    )
    message_id = models.CharField(max_length=512, blank=True, default='')
    imap_uid = models.BigIntegerField()
    subject = models.CharField(max_length=1000, blank=True, default='')

    from_address = models.JSONField(default=dict)
    to_addresses = models.JSONField(default=list)
    cc_addresses = models.JSONField(default=list)
    bcc_addresses = models.JSONField(default=list)
    reply_to = models.CharField(max_length=255, blank=True, default='')

    date = models.DateTimeField(null=True, blank=True)
    snippet = models.CharField(max_length=300, blank=True, default='')
    body_text = models.TextField(blank=True, default='')
    body_html = models.TextField(blank=True, default='')

    is_read = models.BooleanField(default=False)
    is_starred = models.BooleanField(default=False)
    is_draft = models.BooleanField(default=False)
    has_attachments = models.BooleanField(default=False)
    has_calendar_event = models.BooleanField(default=False)

    deleted_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date']
        constraints = [
            models.UniqueConstraint(
                fields=['folder', 'imap_uid'],
                name='unique_mail_message_uid',
            ),
        ]
        indexes = [
            models.Index(fields=['folder', '-date']),
            models.Index(fields=['account', 'message_id']),
            models.Index(fields=['folder', 'imap_uid']),
        ]

    def __str__(self):
        return self.subject or '(no subject)'


def mail_attachment_path(instance, filename):
    account = instance.message.account
    return f'mail/attachments/{account.owner_id}/{account.pk}/{filename}'


class MailAttachment(models.Model):
    """Attachment linked to a mail message."""

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    message = models.ForeignKey(
        MailMessage,
        on_delete=models.CASCADE,
        related_name='attachments',
    )
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=255, default='application/octet-stream')
    size = models.BigIntegerField(default=0)
    content = models.FileField(upload_to=mail_attachment_path)
    content_id = models.CharField(max_length=255, blank=True, default='')
    is_inline = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['filename']

    def __str__(self):
        return self.filename
