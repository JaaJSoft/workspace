from django.test import SimpleTestCase

from workspace.common.encryption import decrypt, encrypt


class EncryptionTests(SimpleTestCase):
    def test_encrypt_decrypt_roundtrip(self):
        plaintext = "my-secret-password"
        ciphertext = encrypt(plaintext)
        self.assertIsInstance(ciphertext, bytes)
        self.assertNotEqual(ciphertext, plaintext.encode())
        self.assertEqual(decrypt(ciphertext), plaintext)

    def test_encrypt_produces_different_ciphertexts(self):
        """Fernet uses a timestamp/IV, so each encryption should differ."""
        c1 = encrypt("same")
        c2 = encrypt("same")
        self.assertNotEqual(c1, c2)

    def test_decrypt_is_inverse_of_encrypt(self):
        for text in ["", "short", "x" * 1000, "unicodé 🎉"]:
            self.assertEqual(decrypt(encrypt(text)), text)
