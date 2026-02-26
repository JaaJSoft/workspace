"""Generate VAPID key pair for Web Push notifications."""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Generate VAPID key pair for Web Push notifications'

    def handle(self, *args, **options):
        private_key = ec.generate_private_key(ec.SECP256R1())

        # PEM-encoded private key (for WEBPUSH_VAPID_PRIVATE_KEY)
        pem = private_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
        ).decode().strip()

        # Raw uncompressed public key, base64url-encoded (for WEBPUSH_VAPID_PUBLIC_KEY)
        raw_public = private_key.public_key().public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint,
        )
        public_b64 = base64.urlsafe_b64encode(raw_public).rstrip(b'=').decode()

        self.stdout.write('\nAdd these to your .env file:\n')
        self.stdout.write(f'WEBPUSH_VAPID_PRIVATE_KEY="{pem}"')
        self.stdout.write(f'WEBPUSH_VAPID_PUBLIC_KEY={public_b64}\n')
