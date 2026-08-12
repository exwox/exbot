#!/usr/bin/env python3
"""Safely set bounded risk values for an existing dry-run bot."""
import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def planned_capital(strategy: dict) -> float:
    base = max(float(strategy.get('base_order_amount', 0) or 0), 0)
    safety = max(float(strategy.get('safety_order_amount', 0) or 0), 0)
    maximum = max(int(strategy.get('max_safety_orders', 0) or 0), 0)
    martingale = bool(strategy.get('martingale_enabled', False))
    scale = max(float(strategy.get('volume_scale', 1) or 1), 0)
    return base + sum(
        safety * (scale ** (level - 1) if martingale else 1)
        for level in range(1, maximum + 1)
    )


def set_bot_risk(database_path: Path, bot_id: str, stop_loss: float,
                 max_position: float, apply: bool = False) -> dict:
    if not 0 < stop_loss <= 100:
        raise ValueError('stop-loss harus lebih dari 0 dan maksimal 100 persen')
    if max_position <= 0:
        raise ValueError('batas posisi harus lebih dari 0')

    connection = sqlite3.connect(str(database_path), timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """SELECT b.id AS bot_id, b.strategy_id AS strategy_id,
                      b.dry_run, b.status, s.*
               FROM bots b JOIN strategies s ON s.id=b.strategy_id
               WHERE b.id=?""",
            (str(bot_id),),
        ).fetchone()
        if not row:
            raise ValueError('bot atau strategi tidak ditemukan')
        strategy = dict(row)
        if not bool(strategy['dry_run']):
            raise ValueError('profil risiko hanya boleh diubah oleh alat ini saat bot dry-run')
        required = planned_capital(strategy)
        if required <= 0:
            raise ValueError('modal siklus tidak valid')
        if max_position < required:
            raise ValueError(
                f'batas posisi {max_position:.0f} di bawah modal siklus {required:.0f}')

        result = {
            'bot_id': str(bot_id),
            'dry_run': True,
            'status': strategy['status'],
            'planned_capital_idr': required,
            'previous_stop_loss_percent': float(
                strategy.get('stop_loss_percent', 0) or 0),
            'previous_max_position_amount': float(
                strategy.get('max_position_amount', 0) or 0),
            'stop_loss_percent': float(stop_loss),
            'max_position_amount': float(max_position),
            'applied': False,
        }
        if apply:
            connection.execute('BEGIN IMMEDIATE')
            cursor = connection.execute(
                """UPDATE strategies
                   SET stop_loss_percent=?, max_position_amount=?, updated_at=?
                   WHERE id=?""",
                (float(stop_loss), float(max_position), utc_now(),
                 strategy['strategy_id']),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise RuntimeError('update profil risiko tidak diterapkan')
            connection.commit()
            result['applied'] = True
        return result
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=(
        os.getenv('DATABASE_PATH') or os.getenv('DB_PATH') or
        'data/dca_bot.db'))
    parser.add_argument('--bot-id', required=True)
    parser.add_argument('--stop-loss', type=float, required=True)
    parser.add_argument('--max-position', type=float, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = set_bot_risk(
        Path(args.database), args.bot_id, args.stop_loss,
        args.max_position, args.apply)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
