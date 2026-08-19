from django.conf import settings
from django.db import models

from workspace.common.uuids import uuid_v7_or_v4


class ImportConnection(models.Model):
    """A remote source a user can import from: a WebDAV/Nextcloud account, later
    an OAuth-connected drive. Stateless providers read it to talk to the remote."""

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="import_connections",
    )
    provider = models.CharField(max_length=50)
    label = models.CharField(max_length=255)

    base_url = models.URLField(max_length=2000, blank=True, default="")
    username = models.CharField(max_length=255, blank=True, default="")
    secret_encrypted = models.BinaryField(null=True, blank=True)
    oauth2_data_encrypted = models.BinaryField(null=True, blank=True)

    # Cached result of the provider's discovery (kinds offered, quota, server
    # version) - refreshed on every connection test, never authoritative.
    capabilities = models.JSONField(default=dict, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["owner", "-created_at"], name="importconn_owner_created"
            ),
        ]

    def __str__(self):
        return f"{self.label} ({self.provider})"

    def set_secret(self, plaintext):
        from workspace.core.encryption import encrypt

        self.secret_encrypted = encrypt(plaintext)

    def get_secret(self):
        from workspace.core.encryption import decrypt

        if not self.secret_encrypted:
            return ""
        return decrypt(bytes(self.secret_encrypted))

    def set_oauth2_data(self, data):
        import orjson

        from workspace.core.encryption import encrypt

        self.oauth2_data_encrypted = encrypt(orjson.dumps(data).decode())

    def get_oauth2_data(self):
        import orjson

        from workspace.core.encryption import decrypt

        if not self.oauth2_data_encrypted:
            return None
        return orjson.loads(decrypt(bytes(self.oauth2_data_encrypted)))


class ImportJob(models.Model):
    """One import run: a connection and the data kinds to pull from it."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"

    TERMINAL_STATUSES = frozenset({Status.COMPLETED, Status.FAILED, Status.CANCELLED})

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="import_jobs",
    )
    connection = models.ForeignKey(
        ImportConnection, on_delete=models.CASCADE, related_name="jobs"
    )

    kinds = models.JSONField(default=list)
    options = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    # Compare-and-swap token claimed by the worker that owns the run; guards
    # against duplicate task delivery (see workspace.common.celery_claim).
    claim_token = models.CharField(max_length=64, blank=True, default="")

    stats = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")
    cancel_requested_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["owner", "status", "-created_at"], name="importjob_owner_status"
            ),
            # Serves the retention purge (terminal jobs by finished_at, no owner).
            models.Index(
                fields=["status", "finished_at"], name="importjob_status_finished"
            ),
        ]

    def __str__(self):
        return f"ImportJob {self.uuid} ({self.status})"

    @property
    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES


class ImportJobItem(models.Model):
    """One remote entry handled by a job. The rows make a job resumable across
    time slices, retryable (only failed entries) and re-runnable incrementally
    (an entry already done with the same etag is skipped)."""

    class Status(models.TextChoices):
        DONE = "done"
        SKIPPED = "skipped"
        FAILED = "failed"

    uuid = models.UUIDField(primary_key=True, default=uuid_v7_or_v4, editable=False)
    job = models.ForeignKey(ImportJob, on_delete=models.CASCADE, related_name="items")
    kind = models.CharField(max_length=30)
    remote_id = models.TextField()
    remote_etag = models.TextField(blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices)
    # The local object created for this entry (a File, later an Event, ...).
    # Not a FK: the target model depends on the kind.
    target_uuid = models.UUIDField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "kind", "remote_id"], name="importjobitem_unique_entry"
            ),
        ]
        indexes = [
            models.Index(fields=["job", "status"], name="importjobitem_job_status"),
        ]

    def __str__(self):
        return f"{self.kind}:{self.remote_id} ({self.status})"
