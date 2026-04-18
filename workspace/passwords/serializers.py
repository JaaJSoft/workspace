from rest_framework import serializers

from .models import LoginEntry, PasswordFolder, Vault


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

class VaultSerializer(serializers.ModelSerializer):
    """Read-only vault representation returned by GET /api/v1/passwords/vaults."""

    class Meta:
        model = Vault
        fields = [
            'uuid',
            'name',
            'description',
            'icon',
            'color',
            'is_setup',
            'kdf_algorithm',
            'kdf_iterations',
            'kdf_salt',
            'kdf_memory',
            'kdf_parallelism',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class VaultSetupResponseSerializer(serializers.ModelSerializer):
    """Response body for a successful vault setup.

    Returned by POST /api/v1/passwords/vaults/<uuid>/setup.
    Includes ``protected_vault_key`` so the client is immediately unlocked
    after setup — no separate /unlock call needed after creation.
    """

    class Meta:
        model = Vault
        fields = [
            'uuid',
            'name',
            'description',
            'icon',
            'color',
            'is_setup',
            'kdf_algorithm',
            'kdf_iterations',
            'kdf_salt',
            'kdf_memory',
            'kdf_parallelism',
            'protected_vault_key',
            'created_at',
            'updated_at',
        ]
        read_only_fields = fields


class VaultCreateSerializer(serializers.Serializer):
    """Payload for POST /api/v1/passwords/vaults.

    Only vault metadata is sent here.  The server pre-generates a composite
    KDF salt (user_uuid_bytes || random) and returns it in the response so the
    client can perform key derivation before calling /setup.
    """

    name = serializers.CharField(
        max_length=100, default='Personal',
        help_text="Display name for this vault (e.g. Personal, Work).",
    )
    description = serializers.CharField(
        required=False, default='', allow_blank=True,
        help_text="Optional description.",
    )
    icon = serializers.CharField(
        max_length=50, required=False, default='vault',
        help_text="Lucide icon name.",
    )
    color = serializers.CharField(
        max_length=50, required=False, default='primary',
        help_text="DaisyUI/Tailwind colour class.",
    )


class VaultSetupSerializer(serializers.Serializer):
    """Payload for POST /api/v1/passwords/vaults/<uuid>/setup.

    Used both for initial setup (after vault creation) and for master-password
    rotation.  On rotation, supply ``kdf_salt`` to replace the server-generated
    salt with a new composite salt derived from the new master password.
    """

    client_master_hash = serializers.CharField(
        help_text=(
            "PBKDF2(stretched_key, master_password, 1) computed client-side. "
            "The server hashes this value before storing — never stored raw."
        )
    )
    protected_vault_key = serializers.CharField(
        help_text="AES-256-GCM vault key encrypted client-side with the stretched master key."
    )
    kdf_salt = serializers.CharField(
        max_length=44,
        required=False,
        allow_blank=True,
        help_text=(
            "Base64url-encoded 32-byte salt. Omit on initial setup — the server reuses "
            "the composite salt generated at vault creation. Supply on master-password "
            "rotation to replace the salt."
        ),
    )
    kdf_algorithm = serializers.ChoiceField(
        choices=Vault.KdfAlgorithm.choices,
        required=False,
        help_text="Key derivation algorithm. Defaults to pbkdf2_sha256.",
    )
    kdf_iterations = serializers.IntegerField(
        required=False, min_value=1,
        help_text="PBKDF2 iteration count. Ignored for Argon2id.",
    )
    kdf_memory = serializers.IntegerField(
        required=False, min_value=1,
        help_text="Argon2id memory cost in KiB. Required when kdf_algorithm is argon2id.",
    )
    kdf_parallelism = serializers.IntegerField(
        required=False, min_value=1,
        help_text="Argon2id parallelism factor. Required when kdf_algorithm is argon2id.",
    )

    def validate(self, attrs):
        algorithm = attrs.get('kdf_algorithm', Vault.KdfAlgorithm.PBKDF2_SHA256)
        if algorithm == Vault.KdfAlgorithm.ARGON2ID:
            if not attrs.get('kdf_memory'):
                raise serializers.ValidationError(
                    {'kdf_memory': 'Required when kdf_algorithm is argon2id.'}
                )
            if not attrs.get('kdf_parallelism'):
                raise serializers.ValidationError(
                    {'kdf_parallelism': 'Required when kdf_algorithm is argon2id.'}
                )
        return attrs


class VaultUnlockSerializer(serializers.Serializer):
    """Payload for POST /api/v1/passwords/vaults/<uuid>/unlock."""

    client_master_hash = serializers.CharField(
        help_text="Client-derived hash to verify against the stored vault hash."
    )


class VaultUnlockResponseSerializer(serializers.Serializer):
    """Response body for a successful vault unlock."""

    protected_vault_key = serializers.CharField(
        help_text="Encrypted vault key. Decrypt client-side with the stretched master key."
    )


# ---------------------------------------------------------------------------
# LoginEntry
# ---------------------------------------------------------------------------

class LoginEntrySerializer(serializers.ModelSerializer):
    """Full representation returned by GET /api/v1/passwords/entries."""

    class Meta:
        model = LoginEntry
        fields = [
            'uuid',
            'vault',
            'folder',
            'type',
            'encrypted_name',
            'icon',
            'icon_color',
            'is_favorite',
            'deleted_at',
            'last_used_at',
            # LoginEntry-specific
            'encrypted_username',
            'encrypted_password',
            'encrypted_totp_secret',
            'uris',
            'notes',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['uuid', 'type', 'deleted_at', 'created_at', 'updated_at']


class LoginEntryCreateSerializer(serializers.ModelSerializer):
    """Payload for POST /api/v1/passwords/entries."""

    class Meta:
        model = LoginEntry
        fields = [
            'uuid',
            'vault',
            'folder',
            'encrypted_name',
            'icon',
            'icon_color',
            'is_favorite',
            # LoginEntry-specific
            'encrypted_username',
            'encrypted_password',
            'encrypted_totp_secret',
            'uris',
            'notes',
        ]
        extra_kwargs = {
            'uuid': {'required': False},
            'folder': {'required': False},
        }

    def validate_vault(self, vault):
        request = self.context['request']
        user = request.user
        if vault.user == user:
            return vault
        from .services.sharing import SharingService
        if SharingService.can_write(vault, user):
            return vault
        raise serializers.ValidationError("You do not have write access to this vault.")

    def create(self, validated_data):
        validated_data['type'] = LoginEntry.EntryType.LOGIN
        return LoginEntry.objects.create(**validated_data)


class LoginEntryUpdateSerializer(serializers.ModelSerializer):
    """Payload for PUT /api/v1/passwords/entries/<uuid>."""

    class Meta:
        model = LoginEntry
        fields = [
            'folder',
            'encrypted_name',
            'icon',
            'icon_color',
            'is_favorite',
            'last_used_at',
            # LoginEntry-specific
            'encrypted_username',
            'encrypted_password',
            'encrypted_totp_secret',
            'uris',
            'notes',
        ]
        extra_kwargs = {
            'folder': {'required': False},
            'encrypted_name': {'required': False},
        }


# ---------------------------------------------------------------------------
# PasswordFolder
# ---------------------------------------------------------------------------

class FolderSerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(read_only=True)

    class Meta:
        model = PasswordFolder
        fields = ['uuid', 'vault', 'parent', 'name', 'icon', 'color', 'order', 'created_at', 'updated_at']
        read_only_fields = ['uuid', 'vault', 'created_at', 'updated_at']


class FolderCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    parent = serializers.UUIDField(required=False, allow_null=True)
    icon = serializers.CharField(max_length=50, required=False, default='folder')
    color = serializers.CharField(max_length=50, required=False, default='')


class FolderUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
