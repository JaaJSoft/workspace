"""Generate VAPID key pair for Web Push notifications."""

import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Generate VAPID key pair for Web Push notifications"

    def handle(self, *args, **options):
        private_key = ec.generate_private_key(ec.SECP256R1())

        # Raw 32-byte private scalar, base64url-encoded. Single-line so it
        # survives .env files and Docker/Kubernetes env vars, none of which
        # handle a multi-line PEM reliably.
        raw_private = private_key.private_numbers().private_value.to_bytes(32, "big")
        private_b64 = base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode()

        # Raw uncompressed public key, base64url-encoded. This is the value the
        # browser receives as applicationServerKey, so changing it invalidates
        # every existing push subscription.
        raw_public = private_key.public_key().public_bytes(
            Encoding.X962,
            PublicFormat.UncompressedPoint,
        )
        public_b64 = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode()

        self.stdout.write("\nAdd these to your .env file:\n")
        self.stdout.write(f"WEBPUSH_VAPID_PRIVATE_KEY={private_b64}")
        self.stdout.write(f"WEBPUSH_VAPID_PUBLIC_KEY={public_b64}")
        self.stdout.write(
            "\nKeep both keys together: the public key is baked into every "
            "browser subscription, so regenerating the pair forces all users "
            "to re-subscribe.\n"
        )
