import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.backup_database import (
    create_backup,
    restore_backup,
    verify_backup,
)


class EncryptedDatabaseBackupTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database = self.root / 'source.db'
        connection = sqlite3.connect(self.database)
        connection.execute('CREATE TABLE secrets (id INTEGER, value TEXT)')
        connection.execute(
            'INSERT INTO secrets VALUES (?, ?)',
            (1, 'plaintext-database-value'),
        )
        connection.commit()
        connection.close()
        self.key = b'backup-test-master-key-at-least-32-bytes'

    def tearDown(self):
        self.tempdir.cleanup()

    def test_backup_verify_restore_and_tamper_detection(self):
        output = self.root / 'backups'
        result = create_backup(self.database, output, self.key, retention=2)
        backup = Path(result['path'])

        self.assertTrue(backup.is_file())
        self.assertTrue(backup.with_suffix('.xbk.json').is_file())
        self.assertNotIn(b'plaintext-database-value', backup.read_bytes())
        self.assertEqual(
            verify_backup(backup, self.key)['quick_check'], 'ok')

        restored = self.root / 'restored.db'
        restore_backup(backup, restored, self.key)
        connection = sqlite3.connect(restored)
        value = connection.execute(
            'SELECT value FROM secrets WHERE id=1').fetchone()[0]
        connection.close()
        self.assertEqual(value, 'plaintext-database-value')
        with self.assertRaises(FileExistsError):
            restore_backup(backup, restored, self.key)

        payload = bytearray(backup.read_bytes())
        payload[-1] ^= 1
        backup.write_bytes(payload)
        with self.assertRaises(ValueError):
            verify_backup(backup, self.key)

    def test_retention_removes_only_old_xbot_backup_pairs(self):
        output = self.root / 'backups'
        first = Path(create_backup(
            self.database, output, self.key, retention=1)['path'])
        unrelated = output / 'keep-me.txt'
        unrelated.write_text('keep', 'utf-8')
        second = Path(create_backup(
            self.database, output, self.key, retention=1)['path'])

        self.assertFalse(first.exists())
        self.assertFalse(first.with_suffix('.xbk.json').exists())
        self.assertTrue(second.exists())
        self.assertTrue(unrelated.exists())


if __name__ == '__main__':
    unittest.main()
