"""Demo vault data, written the way a browser writes it.

Every row here is sealed and signed for real, through the same reference
implementation the crypto vectors are generated from. That is the point: a
seed that wrote plausible-looking base64 would produce a vault the browser
refuses to open, and the tamper banner would be the first thing a demo shows.

Because the account identity is derived from a master password, the seeded
account has one - printed at the end - and it is deliberately the same for
every seeded user. Nothing here belongs anywhere but a demo database.

It lives beside seed_demo.py rather than inside the module: the module ships
no way to write a vault from the server side, and it must not grow one. A
server able to mint a signature is a server able to forge one.
"""

import secrets

from django.utils import timezone

from workspace.common.uuids import uuid_v7_or_v4
from workspace.vault.models import (
    AccountIdentity,
    EntryField,
    EntryType,
    Vault,
    VaultEntry,
    VaultFolder,
    VaultKeyWrap,
    VaultTag,
)
from workspace.vault.tests.reference import ad, metadata, primitives, wire
from workspace.vault.tests.reference.encoding import to_base64url

MASTER_PASSWORD = "demo-vault-1234"

# The suite the wrap was produced with, as the column stores it: a dict, not
# the CipherSuite object the reference works in. It is what tells a future
# reader which primitives to open this wrap with.
HPKE_SUITE_V1 = {"kem_id": 32, "kdf_id": 1, "aead_id": 2, "mode": 0}

# The demo content. Two vaults, one with a tree and a trash, one flat - enough
# for every view the browser offers to have something in it.
VAULTS = [
    {
        "name": "Personal",
        "icon": "lock",
        "color": "primary",
        "favorite": True,
        "folders": [("Subscriptions", None), ("Banking", None), ("Cards", "Banking")],
        "tags": [("Personal", "#3b82f6"), ("Money", "#22c55e"), ("Media", "#a855f7")],
        "entries": [
            (
                "GitHub",
                "octocat",
                "gh-9f3a-correct-horse",
                None,
                ["Personal"],
                True,
                False,
            ),
            (
                "Bank of Somewhere",
                "4021 9987",
                "b4nk-tr0ub4dor-3",
                "Banking",
                ["Money"],
                True,
                False,
            ),
            (
                "Visa ending 4242",
                "4242 4242 4242 4242",
                "card-pin-8891",
                "Cards",
                ["Money"],
                False,
                False,
            ),
            (
                "Netflix",
                "famille@example.org",
                "n3tflix-popcorn",
                "Subscriptions",
                ["Media"],
                False,
                False,
            ),
            (
                "Spotify",
                "jc@example.org",
                "sp0tify-playlist",
                "Subscriptions",
                ["Media"],
                False,
                False,
            ),
            (
                "Old forum account",
                "jc_2009",
                "forgotten-password",
                None,
                [],
                False,
                True,
            ),
        ],
    },
    {
        "name": "Work",
        "icon": "briefcase",
        "color": "info",
        "favorite": False,
        "folders": [],
        "tags": [("Infra", "#f97316"), ("Vendors", "#06b6d4")],
        "entries": [
            (
                "AWS console",
                "root@acme.example",
                "aws-r00t-do-not-share",
                None,
                ["Infra"],
                True,
                False,
            ),
            (
                "Grafana",
                "jc@acme.example",
                "gr4fana-dashboards",
                None,
                ["Infra"],
                False,
                False,
            ),
            (
                "Datadog",
                "jc@acme.example",
                "d4tadog-metrics",
                None,
                ["Vendors"],
                False,
                False,
            ),
        ],
    },
]


class _Signer:
    """One account's keys, held for the length of the seed and no longer.

    A browser derives these from the master password and keeps them in a
    closure. Here they live in an object that goes out of scope when the seed
    finishes - the same lifetime, for the same reason.
    """

    def __init__(self, user):
        self.user = user
        self.account_uuid = uuid_v7_or_v4()
        self.salt = secrets.token_bytes(primitives.SALT_LENGTH)
        self.secret_key = secrets.token_bytes(primitives.SECRET_KEY_LENGTH)
        amk = primitives.derive_amk(MASTER_PASSWORD, self.secret_key, self.salt)
        # The account master key never seals anything itself: one HKDF step
        # separates it from the key that wraps the private halves, so a future
        # use of the AMK cannot collide with this one.
        self.unwrap_key = primitives.hkdf(amk, ad.unwrap_info())
        self.kex_private = primitives.generate_kex_keypair()
        self.sig_private = primitives.generate_sig_keypair()

    # ---- the account envelope --------------------------------------------

    def seal(self, key, plaintext, associated_data, kdf_id=wire.KDF_HKDF_SHA256):
        return to_base64url(
            primitives.aead_seal(
                key,
                plaintext,
                associated_data,
                iv=secrets.token_bytes(12),
                key_version=1,
                kdf_id=kdf_id,
            )
        )

    @property
    def recovery_key(self):
        """What the emergency kit prints, in the form the unlock screen takes."""
        return primitives.crockford_encode(self.secret_key)

    def write_identity(self):
        account = str(self.account_uuid)
        kex_public = primitives.encode_public_key(
            self.kex_private.public_key(), primitives.PUBKEY_ALG_X25519
        )
        sig_public = primitives.encode_public_key(
            self.sig_private.public_key(), primitives.PUBKEY_ALG_ED25519
        )
        identity = AccountIdentity.objects.create(
            uuid=self.account_uuid,
            user=self.user,
            # Without these the browser cannot reproduce the derivation: the
            # envelope is what tells it which cost parameters produced the key.
            kdf_params=primitives.ARGON2_PARAMS,
            kdf_salt=to_base64url(self.salt),
            kex_public=to_base64url(kex_public),
            sig_public=to_base64url(sig_public),
            # The private halves are sealed under the account master key, so
            # only the master password and the recovery key reach them.
            wrapped_kex_priv=self.seal(
                self.unwrap_key,
                primitives.private_bytes(self.kex_private),
                ad.kex_priv_ad(account),
                kdf_id=wire.KDF_DIRECT,
            ),
            wrapped_sig_priv=self.seal(
                self.unwrap_key,
                primitives.private_bytes(self.sig_private),
                ad.sig_priv_ad(account),
                kdf_id=wire.KDF_DIRECT,
            ),
            # What binds the exchange key to the signing key: without it the
            # server could hand out a key of its own and read every wrap.
            sig_over_kex_pub=to_base64url(
                primitives.sign_bytes(
                    self.sig_private,
                    ad.kex_pub_payload(account, to_base64url(kex_public)),
                )
            ),
            state=AccountIdentity.State.ACTIVE,
        )
        return identity

    def sign(self, payload):
        return to_base64url(primitives.sign(self.sig_private, payload))


def _vault_keys(signer, vault_uuid):
    """The vault key, plus the two keys derived from it.

    One random key per vault, wrapped to the account's exchange key. The
    metadata key and every entry key are HKDF derivations of it, so a member
    who can open the wrap can open the vault and nothing outside it.
    """
    vault_key = secrets.token_bytes(primitives.AEAD_KEY_LENGTH)
    meta_key = primitives.hkdf(vault_key, ad.vault_meta_info(vault_uuid))
    wrapped = primitives.hpke_seal(
        signer.kex_private.public_key(),
        ad.vault_key_info(vault_uuid, str(signer.account_uuid)),
        vault_key,
        # HPKE draws a fresh key per wrap; the reference takes it as an
        # argument so a published vector can pin one. Here it is simply new.
        sender_private=primitives.generate_kex_keypair(),
    )
    return vault_key, meta_key, to_base64url(wrapped)


def seed_vault_for(user):
    """Write every demo vault for *user*.

    Returns a summary, including the recovery key: without it the seeded vault
    cannot be opened, because the master password is only half of what the
    key derivation takes.
    """
    if AccountIdentity.objects.filter(user=user).exists():
        return {"vaults": 0, "entries": 0, "recovery_key": None}

    signer = _Signer(user)
    signer.write_identity()
    account = str(signer.account_uuid)
    counts = {"vaults": 0, "entries": 0, "recovery_key": signer.recovery_key}

    for spec in VAULTS:
        vault_uuid = uuid_v7_or_v4()
        vault_key, meta_key, wrapped_key = _vault_keys(signer, str(vault_uuid))
        encrypted_name = signer.seal(
            meta_key, spec["name"].encode(), ad.vault_field_ad(str(vault_uuid), "name")
        )
        vault = Vault.objects.create(
            uuid=vault_uuid,
            owner=user,
            encrypted_name=encrypted_name,
            encrypted_description="",
            icon=spec["icon"],
            color=spec["color"],
            key_version=1,
            is_favorite=spec["favorite"],
            metadata_sig=signer.sign(
                metadata.vault_metadata_payload(
                    vault_uuid=str(vault_uuid),
                    owner_account_uuid=account,
                    encrypted_name=encrypted_name,
                    encrypted_description="",
                    icon=spec["icon"],
                    color=spec["color"],
                    key_version=1,
                    is_favorite=spec["favorite"],
                )
            ),
        )
        VaultKeyWrap.objects.create(
            vault=vault,
            recipient=user,
            wrapped_key=wrapped_key,
            key_version=1,
            hpke_suite=HPKE_SUITE_V1,
        )
        counts["vaults"] += 1

        folders = {}
        for position, (name, parent_name) in enumerate(spec["folders"]):
            folder_uuid = uuid_v7_or_v4()
            parent = folders.get(parent_name)
            sealed = signer.seal(
                meta_key, name.encode(), ad.folder_field_ad(str(folder_uuid), "name")
            )
            folders[name] = VaultFolder.objects.create(
                uuid=folder_uuid,
                vault=vault,
                parent=parent,
                encrypted_name=sealed,
                position=position,
                metadata_sig=signer.sign(
                    metadata.folder_metadata_payload(
                        folder_uuid=str(folder_uuid),
                        vault_uuid=str(vault_uuid),
                        signer_account_uuid=account,
                        parent_uuid=str(parent.uuid) if parent else None,
                        encrypted_name=sealed,
                        position=position,
                    )
                ),
            )

        tags = {}
        for name, color in spec["tags"]:
            tag_uuid = uuid_v7_or_v4()
            sealed = signer.seal(
                meta_key, name.encode(), ad.tag_field_ad(str(tag_uuid), "name")
            )
            tags[name] = VaultTag.objects.create(
                uuid=tag_uuid,
                vault=vault,
                encrypted_name=sealed,
                color=color,
                metadata_sig=signer.sign(
                    metadata.tag_metadata_payload(
                        tag_uuid=str(tag_uuid),
                        vault_uuid=str(vault_uuid),
                        signer_account_uuid=account,
                        encrypted_name=sealed,
                        color=color,
                    )
                ),
            )

        for name, username, password, folder_name, tag_names, favorite, trashed in spec[
            "entries"
        ]:
            entry_uuid = uuid_v7_or_v4()
            entry_key = primitives.hkdf(vault_key, ad.entry_key_info(str(entry_uuid)))

            def seal_field(field, value, entry_uuid=entry_uuid, entry_key=entry_key):
                return signer.seal(
                    entry_key,
                    value.encode(),
                    ad.entry_field_ad(str(entry_uuid), field),
                )

            fields = {
                "username": seal_field("username", username),
                "password": seal_field("password", password),
            }
            encrypted_name = seal_field("name", name)
            folder = folders.get(folder_name)
            tag_rows = [tags[label] for label in tag_names]
            entry = VaultEntry.objects.create(
                uuid=entry_uuid,
                vault=vault,
                type=EntryType.LOGIN,
                folder=folder,
                encrypted_name=encrypted_name,
                encrypted_notes="",
                key_version=1,
                entry_version=1,
                is_favorite=favorite,
                deleted_at=timezone.now() if trashed else None,
                metadata_sig=signer.sign(
                    metadata.entry_metadata_payload(
                        entry_uuid=str(entry_uuid),
                        vault_uuid=str(vault_uuid),
                        signer_account_uuid=account,
                        entry_type=EntryType.LOGIN,
                        folder_uuid=str(folder.uuid) if folder else None,
                        encrypted_name=encrypted_name,
                        encrypted_notes="",
                        key_version=1,
                        entry_version=1,
                        is_favorite=favorite,
                        tag_uuids=[str(tag.uuid) for tag in tag_rows],
                        fields=fields,
                    )
                ),
            )
            entry.tags.set(tag_rows)
            EntryField.objects.bulk_create(
                EntryField(entry=entry, field_id=field_id, encrypted_value=value)
                for field_id, value in fields.items()
            )
            counts["entries"] += 1

    return counts


__all__ = ["MASTER_PASSWORD", "seed_vault_for"]
