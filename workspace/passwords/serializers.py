from rest_framework import serializers

from .models import LoginEntry, Vault


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------

class VaultSerializer(serializers.ModelSerializer):
    """Read-only vault metadata returned by GET /api/v1/passwords/vault."""

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


class VaultSetupSerializer(serializers.Serializer):
    """Payload for POST /api/v1/passwords/vault/setup."""

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
        help_text="Base64url-encoded 32-byte salt used for PBKDF2 key derivation."
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
    """Payload for POST /api/v1/passwords/vault/unlock."""

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
        if vault.user != request.user:
            raise serializers.ValidationError("Vault does not belong to you.")
        return vault

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
