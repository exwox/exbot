"""Migrate account credentials to the authenticated v2 AES-GCM format."""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from services.encryption_service import EncryptionService


def migrate(database_path: str, dry_run: bool = False, key: str | None = None) -> int:
    encryption = EncryptionService(key)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    migrated = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT id, api_key_encrypted, api_secret_encrypted FROM accounts"
        ).fetchall()
        for row in rows:
            updates = []
            for column in ("api_key_encrypted", "api_secret_encrypted"):
                payload = row[column] or ""
                if not payload or payload.startswith("v2:"):
                    continue
                plaintext = encryption.decrypt(payload)
                replacement = encryption.encrypt(plaintext)
                if encryption.decrypt(replacement) != plaintext:
                    raise RuntimeError(f"Round-trip verification failed for account {row['id']}")
                updates.append((column, replacement))
            if updates:
                assignments = ", ".join(f"{column}=?" for column, _ in updates)
                values = [value for _, value in updates] + [row["id"]]
                connection.execute(
                    f"UPDATE accounts SET {assignments} WHERE id=?", values
                )
                migrated += 1
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
        return migrated
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database", default=os.getenv("DATABASE_PATH", "data/dca_bot.db")
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    count = migrate(args.database, args.dry_run)
    mode = "would migrate" if args.dry_run else "migrated"
    print(f"{mode} {count} account credential record(s)")


if __name__ == "__main__":
    main()
