import subprocess
import unittest

from cryptography.fernet import Fernet

from services.encryption_service import EncryptionService


class EncryptionBridgeTest(unittest.TestCase):
    key = "test-master-key-with-at-least-32-bytes"
    plaintext = "credential-secret-123"

    def node(self, command, payload):
        return subprocess.check_output(
            ["node", "tests/crypto_bridge.cjs", command, self.key, payload],
            text=True,
        )

    def test_node_encrypts_python_decrypts(self):
        payload = self.node("encrypt", self.plaintext)
        self.assertEqual(EncryptionService(self.key).decrypt(payload), self.plaintext)

    def test_python_encrypts_node_decrypts(self):
        payload = EncryptionService(self.key).encrypt(self.plaintext)
        self.assertEqual(self.node("decrypt", payload), self.plaintext)

    def test_tampering_is_rejected(self):
        service = EncryptionService(self.key)
        payload = service.encrypt(self.plaintext)
        tampered = payload[:-1] + ("0" if payload[-1] != "0" else "1")
        with self.assertRaises(ValueError):
            service.decrypt(tampered)

    def test_legacy_fernet_decrypts_in_node(self):
        legacy_key = Fernet.generate_key().decode()
        payload = Fernet(legacy_key.encode()).encrypt(self.plaintext.encode()).decode()
        output = subprocess.check_output(
            ["node", "tests/crypto_bridge.cjs", "decrypt", legacy_key, payload],
            text=True,
        )
        self.assertEqual(output, self.plaintext)


if __name__ == "__main__":
    unittest.main()
