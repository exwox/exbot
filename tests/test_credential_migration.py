import hashlib
import os
import sqlite3
import tempfile
import unittest

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from scripts.migrate_credentials import migrate
from services.encryption_service import EncryptionService


class CredentialMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = os.path.join(self.temp.name, 'migration.db')
        self.key = Fernet.generate_key().decode()
        connection = sqlite3.connect(self.database)
        connection.execute(
            'CREATE TABLE accounts (id TEXT PRIMARY KEY, api_key_encrypted TEXT, api_secret_encrypted TEXT)'
        )
        connection.close()

    def tearDown(self):
        self.temp.cleanup()

    def legacy_cbc(self, plaintext):
        iv = os.urandom(16)
        key = hashlib.scrypt(
            self.key.encode(), salt=b'salt', n=16384, r=8, p=1, dklen=32,
            maxmem=64 * 1024 * 1024,
        )
        padder = padding.PKCS7(algorithms.AES.block_size).padder()
        padded = padder.update(plaintext.encode()) + padder.finalize()
        encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
        return f'{iv.hex()}:{(encryptor.update(padded) + encryptor.finalize()).hex()}'

    def test_migrates_cbc_and_fernet_transactionally(self):
        connection = sqlite3.connect(self.database)
        connection.executemany(
            'INSERT INTO accounts VALUES (?, ?, ?)',
            [
                ('cbc', self.legacy_cbc('cbc-key'), self.legacy_cbc('cbc-secret')),
                ('fernet', Fernet(self.key.encode()).encrypt(b'fernet-key').decode(),
                 Fernet(self.key.encode()).encrypt(b'fernet-secret').decode()),
            ],
        )
        connection.commit()
        connection.close()

        self.assertEqual(migrate(self.database, dry_run=True, key=self.key), 2)
        connection = sqlite3.connect(self.database)
        self.assertFalse(connection.execute('SELECT api_key_encrypted FROM accounts LIMIT 1').fetchone()[0].startswith('v2:'))
        connection.close()

        self.assertEqual(migrate(self.database, key=self.key), 2)
        service = EncryptionService(self.key)
        connection = sqlite3.connect(self.database)
        rows = connection.execute('SELECT id, api_key_encrypted, api_secret_encrypted FROM accounts ORDER BY id').fetchall()
        connection.close()
        self.assertTrue(all(row[1].startswith('v2:') and row[2].startswith('v2:') for row in rows))
        self.assertEqual(service.decrypt(rows[0][1]), 'cbc-key')
        self.assertEqual(service.decrypt(rows[1][2]), 'fernet-secret')
        self.assertEqual(migrate(self.database, key=self.key), 0)


if __name__ == '__main__':
    unittest.main()
