#!/usr/bin/env python3
"""Read-only arithmetic audit of dry-run cycles against their trade ledger."""
import argparse
import json
import os
import sqlite3
from pathlib import Path


ACTIVE_TOLERANCE_IDR = 0.01
ACTIVE_TOLERANCE_AMOUNT = 1e-10


def close_enough(actual: float, expected: float, tolerance: float) -> bool:
    return abs(float(actual or 0) - float(expected or 0)) <= tolerance


def audit_dry_run_cycles(database_path: Path, bot_id: str,
                         require_closed: int = 1) -> dict:
    uri = f"file:{database_path.resolve()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        bot = connection.execute(
            "SELECT id, status, dry_run FROM bots WHERE id=?", (bot_id,)
        ).fetchone()
        if not bot:
            raise ValueError('bot tidak ditemukan')
        cycles = connection.execute(
            """SELECT * FROM dca_cycles
               WHERE bot_id=? AND dry_run=1 ORDER BY started_at""",
            (bot_id,),
        ).fetchall()
        reports = []
        for row in cycles:
            cycle = dict(row)
            totals = dict(connection.execute(
                """SELECT
                       COALESCE(SUM(CASE WHEN side='buy' THEN amount_quote ELSE 0 END), 0) buy_quote,
                       COALESCE(SUM(CASE WHEN side='buy' THEN amount ELSE 0 END), 0) buy_amount,
                       COALESCE(SUM(CASE WHEN side='sell' THEN amount_quote ELSE 0 END), 0) sell_net,
                       COALESCE(SUM(CASE WHEN side='sell' THEN amount_quote+fee ELSE 0 END), 0) sell_gross,
                       COALESCE(SUM(fee), 0) fees,
                       COALESCE(SUM(CASE WHEN side='sell' THEN realized_profit ELSE 0 END), 0) profit,
                       SUM(CASE WHEN side='sell' AND close_reason IN ('TAKE_PROFIT','STOP_LOSS') THEN 1 ELSE 0 END) close_trades,
                       COUNT(*) trade_count
                   FROM trades WHERE position_id=? AND bot_id=? AND dry_run=1""",
                (cycle['id'], bot_id),
            ).fetchone())
            checks = {
                'total_invested': close_enough(
                    cycle['total_invested'], totals['buy_quote'], ACTIVE_TOLERANCE_IDR),
                'total_amount': close_enough(
                    cycle['total_amount'], totals['buy_amount'], ACTIVE_TOLERANCE_AMOUNT),
                'gross_exit_value': close_enough(
                    cycle['gross_exit_value'], totals['sell_gross'], ACTIVE_TOLERANCE_IDR),
                'net_exit_value': close_enough(
                    cycle['net_exit_value'], totals['sell_net'], ACTIVE_TOLERANCE_IDR),
                'total_fees': close_enough(
                    cycle['total_fees'], totals['fees'], ACTIVE_TOLERANCE_IDR),
                'realized_profit': close_enough(
                    cycle['realized_profit'], totals['profit'], ACTIVE_TOLERANCE_IDR),
                'strategy_close_has_exit_trade': (
                    cycle['close_reason'] not in ('TAKE_PROFIT', 'STOP_LOSS')
                    or totals['close_trades'] > 0),
            }
            reports.append({
                'cycle_id': cycle['id'], 'status': cycle['status'],
                'close_reason': cycle['close_reason'],
                'trade_count': totals['trade_count'],
                'checks': checks, 'valid': all(checks.values()),
            })
        closed_valid = sum(
            report['status'] == 'CLOSED' and report['valid']
            and report['close_reason'] in ('TAKE_PROFIT', 'STOP_LOSS')
            for report in reports)
        return {
            'bot_id': bot_id, 'bot_dry_run': bool(bot['dry_run']),
            'cycle_count': len(reports), 'valid_closed_cycles': closed_valid,
            'required_closed_cycles': max(int(require_closed), 0),
            'valid': (bool(bot['dry_run'])
                      and all(report['valid'] for report in reports)
                      and closed_valid >= max(int(require_closed), 0)),
            'cycles': reports,
        }
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default=(
        os.getenv('DATABASE_PATH') or os.getenv('DB_PATH') or 'data/dca_bot.db'))
    parser.add_argument('--bot-id', required=True)
    parser.add_argument('--require-closed', type=int, default=1)
    args = parser.parse_args()
    if args.require_closed < 0:
        parser.error('--require-closed tidak boleh negatif')
    result = audit_dry_run_cycles(
        Path(args.database), args.bot_id, args.require_closed)
    print(json.dumps(result, indent=2))
    return 0 if result['valid'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
