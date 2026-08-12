#!/usr/bin/env python3
"""Create, verify, and restore encrypted online SQLite backups for XBot."""
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from dotenv import load_dotenv


load_dotenv()


MAGIC = b'XBOTBKP1'
SALT_SIZE = 16
NONCE_SIZE = 12


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def require_master_key(value: str | None = None) -> bytes:
    key = (value if value is not None else
           os.getenv('BACKUP_ENCRYPTION_KEY') or
           os.getenv('ENCRYPTION_KEY') or '')
    encoded = key.encode('utf-8')
    if len(encoded) < 16:
        raise ValueError(
            'BACKUP_ENCRYPTION_KEY atau ENCRYPTION_KEY minimal 16 byte')
    return encoded


def derive_key(master_key: bytes, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2 ** 14, r=8, p=1).derive(
        master_key)


def quick_check(database_path: Path) -> None:
    connection = sqlite3.connect(str(database_path))
    try:
        result = connection.execute('PRAGMA quick_check').fetchone()
        if not result or str(result[0]).lower() != 'ok':
            raise ValueError(f'SQLite quick_check gagal: {result}')
    finally:
        connection.close()


def create_sqlite_snapshot(source_path: Path, target_path: Path) -> None:
    resolved = source_path.resolve()

    def copy_from(uri: str) -> None:
        source = sqlite3.connect(uri, uri=True, timeout=30)
        destination = sqlite3.connect(str(target_path))
        try:
            source.backup(destination)
            destination.commit()
        finally:
            destination.close()
            source.close()

    try:
        copy_from(f"file:{resolved}?mode=ro")
    except sqlite3.OperationalError as error:
        # SQLite WAL readers normally need write access to the directory for
        # shared-memory locks. A stopped/read-only database with no WAL can be
        # copied safely in immutable mode (useful for host-owned bind mounts).
        wal = Path(f'{resolved}-wal')
        shm = Path(f'{resolved}-shm')
        live_sidecars = any(path.exists() and path.stat().st_size > 0
                            for path in (wal, shm))
        if 'readonly' not in str(error).lower() or live_sidecars:
            raise RuntimeError(
                'Snapshot gagal; jalankan sebagai user container pemilik '
                'database dan jangan gunakan immutable saat WAL aktif') from error
        target_path.unlink(missing_ok=True)
        copy_from(f"file:{resolved}?mode=ro&immutable=1")
    quick_check(target_path)


def encrypt_snapshot(snapshot_path: Path, backup_path: Path,
                     master_key: bytes) -> None:
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    plaintext = snapshot_path.read_bytes()
    ciphertext = AESGCM(derive_key(master_key, salt)).encrypt(
        nonce, plaintext, MAGIC)
    temporary = backup_path.with_suffix(backup_path.suffix + '.tmp')
    temporary.write_bytes(MAGIC + salt + nonce + ciphertext)
    os.chmod(temporary, 0o600)
    os.replace(temporary, backup_path)


def decrypt_backup(backup_path: Path, target_path: Path,
                   master_key: bytes) -> None:
    payload = backup_path.read_bytes()
    header_size = len(MAGIC) + SALT_SIZE + NONCE_SIZE
    if len(payload) <= header_size or not payload.startswith(MAGIC):
        raise ValueError('Format backup XBot tidak valid')
    offset = len(MAGIC)
    salt = payload[offset:offset + SALT_SIZE]
    offset += SALT_SIZE
    nonce = payload[offset:offset + NONCE_SIZE]
    ciphertext = payload[offset + NONCE_SIZE:]
    plaintext = AESGCM(derive_key(master_key, salt)).decrypt(
        nonce, ciphertext, MAGIC)
    target_path.write_bytes(plaintext)
    os.chmod(target_path, 0o600)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_backup(backup_path: Path, master_key: bytes) -> dict:
    backup_path = backup_path.resolve()
    manifest_path = backup_path.with_suffix(backup_path.suffix + '.json')
    manifest = json.loads(manifest_path.read_text('utf-8')) \
        if manifest_path.exists() else {}
    expected = manifest.get('sha256')
    actual = sha256_file(backup_path)
    if expected and expected != actual:
        raise ValueError('Checksum backup tidak cocok')
    handle, temporary_name = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        decrypt_backup(backup_path, temporary, master_key)
        quick_check(temporary)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        'backup': str(backup_path), 'sha256': actual,
        'quick_check': 'ok', 'verified_at': utc_now(),
    }


def prune_backups(output_dir: Path, retention: int) -> list[str]:
    if retention <= 0:
        return []
    backups = sorted(output_dir.glob('xbot-*.xbk'),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    removed = []
    for backup in backups[retention:]:
        manifest = backup.with_suffix(backup.suffix + '.json')
        backup.unlink()
        manifest.unlink(missing_ok=True)
        removed.append(backup.name)
    return removed


def create_backup(database_path: Path, output_dir: Path, master_key: bytes,
                  retention: int = 14) -> dict:
    database_path = database_path.resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f'Database tidak ditemukan: {database_path}')
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup_path = output_dir / f'xbot-{timestamp}-{uuid.uuid4().hex[:8]}.xbk'
    handle, temporary_name = tempfile.mkstemp(suffix='.db')
    os.close(handle)
    snapshot = Path(temporary_name)
    try:
        create_sqlite_snapshot(database_path, snapshot)
        encrypt_snapshot(snapshot, backup_path, master_key)
    finally:
        snapshot.unlink(missing_ok=True)
    verification = verify_backup(backup_path, master_key)
    manifest = {
        'format': 'XBOTBKP1', 'created_at': utc_now(),
        'source_name': database_path.name,
        'backup_name': backup_path.name,
        'encrypted_size': backup_path.stat().st_size,
        'sha256': verification['sha256'], 'quick_check': 'ok',
    }
    manifest_path = backup_path.with_suffix(backup_path.suffix + '.json')
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n', 'utf-8')
    os.chmod(manifest_path, 0o600)
    removed = prune_backups(output_dir, retention)
    return {**manifest, 'path': str(backup_path), 'pruned': removed}


def restore_backup(backup_path: Path, target_path: Path, master_key: bytes,
                   force: bool = False) -> dict:
    backup_path = backup_path.resolve()
    target_path = target_path.resolve()
    if target_path.exists() and not force:
        raise FileExistsError(
            f'Target sudah ada: {target_path}; gunakan --force secara eksplisit')
    target_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        suffix='.db', dir=str(target_path.parent))
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        decrypt_backup(backup_path, temporary, master_key)
        quick_check(temporary)
        os.replace(temporary, target_path)
        os.chmod(target_path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return {'restored_to': str(target_path), 'quick_check': 'ok'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=(
        os.getenv('DATABASE_PATH') or os.getenv('DB_PATH') or
        'data/dca_bot.db'))
    parser.add_argument('--output-dir', default=os.getenv(
        'BACKUP_DIR', 'backups'))
    parser.add_argument('--retention', type=int, default=int(os.getenv(
        'BACKUP_RETENTION', '14')))
    parser.add_argument('--verify', metavar='BACKUP')
    parser.add_argument('--restore', metavar='BACKUP')
    parser.add_argument('--restore-target')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    master_key = require_master_key()
    if args.verify:
        result = verify_backup(Path(args.verify), master_key)
    elif args.restore:
        if not args.restore_target:
            parser.error('--restore-target wajib digunakan bersama --restore')
        result = restore_backup(
            Path(args.restore), Path(args.restore_target), master_key,
            args.force)
    else:
        result = create_backup(
            Path(args.database), Path(args.output_dir), master_key,
            args.retention)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
