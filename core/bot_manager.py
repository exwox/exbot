"""
Bot Manager - Mengelola semua akun dan bot worker
- Load semua akun aktif
- Load semua bot aktif
- Membuat client exchange per akun
- Membuat worker per bot
- Menjalankan/menghentikan worker
- Monitoring health
- Error isolation antar worker
"""
import logging
import threading
import time
from datetime import datetime
from typing import Optional

from database.database import DatabaseManager
from services.account_service import AccountService
from services.encryption_service import EncryptionService
from exchanges.indodax_client import IndodaxClient
from core.bot_worker import BotWorker
from config.constants import BotStatus, AccountStatus
from config.settings import live_trading_allowed_for


class BotManager:
    """
    Bot Manager - entry point utama untuk multi-account bot system.
    Mengelola lifecycle seluruh worker.
    """

    def __init__(self, db: DatabaseManager, encryption: EncryptionService):
        self.db = db
        self.account_service = AccountService(db, encryption)
        self.encryption = encryption
        self.workers: dict[str, BotWorker] = {}  # bot_id -> BotWorker
        self._lock = threading.Lock()
        self._logger = logging.getLogger("BotManager")

    def initialize(self):
        """Initialize all accounts and bots"""
        self._logger.info("Initializing BotManager...")
        self.load_all_workers()

    def load_all_workers(self):
        """Load all active bots as workers"""
        active_accounts = self.account_service.get_active_accounts()
        self._logger.info(f"Found {len(active_accounts)} active accounts")

        for account in active_accounts:
            self._load_account_workers(account)

    def reconcile_workers(self):
        """Synchronize Python workers with bot status changed by the dashboard.

        The Node dashboard is a separate process, so it communicates intent
        through SQLite.  A RUNNING bot gets one worker; a STOPPED bot has its
        worker stopped.  Workers remain keyed by bot_id, keeping accounts and
        DCA strategies isolated without spawning a Python terminal per bot.
        """
        active_accounts = self.account_service.get_active_accounts()
        active_account_ids = {account.id for account in active_accounts}

        for account in active_accounts:
            user = self.db.get_user(account.user_id) if hasattr(account, 'user_id') and account.user_id else None
            is_user_valid = True
            if user:
                if not user.get('is_active'):
                    is_user_valid = False
                elif user.get('expired_at'):
                    exp_str = str(user['expired_at']).strip() if isinstance(user['expired_at'], str) else ''
                    if exp_str:
                        try:
                            exp_time = datetime.fromisoformat(exp_str) if 'T' in exp_str else datetime.fromisoformat(exp_str + 'T23:59:59')
                            if datetime.now() > exp_time:
                                is_user_valid = False
                        except Exception:
                            pass

            for bot_data in self.db.get_account_bots(account.id):
                bot_id = bot_data['id']
                should_run = bot_data.get('status') == 'RUNNING' and is_user_valid

                if not is_user_valid and bot_data.get('status') == 'RUNNING':
                    self._logger.warning(f"Stopping bot {bot_id}: User account {account.user_id} is inactive or subscription expired.")
                    bot_data['status'] = 'STOPPED'
                    self.db.update_bot(bot_data)

                with self._lock:
                    worker = self.workers.get(bot_id)

                if should_run:
                    if worker is None:
                        self._logger.info(f"Starting requested bot worker: {bot_id}")
                        self._start_single_worker(account, bot_data)
                    else:
                        # Settings are saved by the Node process. Refresh the
                        # worker every reconciliation cycle so Martingale,
                        # volume scale, and the other DCA values take effect
                        # for this bot without affecting other workers.
                        worker.update_strategy_config(
                            self._get_strategy_config(bot_data.get('strategy_id')))
                        if worker.status != BotStatus.RUNNING:
                            self._logger.info(f"Resuming requested bot worker: {bot_id}")
                            worker.start()
                elif worker and worker.status != BotStatus.STOPPED:
                    self._logger.info(f"Stopping requested bot worker: {bot_id}")
                    worker.stop()

        # Stop workers whose account was disabled or deleted.
        with self._lock:
            orphaned_workers = [worker for worker in self.workers.values()
                                if worker.account_id not in active_account_ids]
        for worker in orphaned_workers:
            worker.stop()

    def _load_account_workers(self, account):
        """Load all bot workers for an account"""
        bots = self.db.get_account_bots(account.id)
        self._logger.info(f"Account {account.name}: {len(bots)} bots")

        for bot_data in bots:
            if bot_data.get('status') in ('RUNNING', 'STARTING'):
                self._start_single_worker(account, bot_data)

    def _start_single_worker(self, account, bot_data: dict) -> Optional[BotWorker]:
        """Create and start a single bot worker"""
        bot_id = bot_data['id']
        pair = bot_data.get('pair', 'btcidr')
        dry_run = bool(bot_data.get('dry_run', True))
        strategy_config = self._get_strategy_config(bot_data.get('strategy_id'))

        completed_dry_cycles = self.db.get_completed_dry_run_cycle_count(bot_id)
        if (not dry_run and not live_trading_allowed_for(
                bot_id, completed_dry_cycles, strategy_config)):
            self._block_live_worker(account.id, bot_data)
            return None
        if not dry_run:
            try:
                self.db.resolve_alert(f'live-gate:{bot_id}')
            except Exception as error:
                self._logger.error("Failed to resolve live gate alert: %s", error)

        # Decrypt credentials
        creds = self.account_service.get_decrypted_credentials(account.id)
        if not creds:
            self._logger.error(f"Failed to decrypt credentials for account {account.id}")
            self._raise_credential_alert(account.id, bot_id, 'worker tidak dijalankan')
            return None
        self._resolve_credential_alert(account.id)

        # Create exchange client for this account
        client = IndodaxClient(creds['api_key'], creds['api_secret'])

        # Create worker
        worker = BotWorker(
            account_id=account.id,
            bot_id=bot_id,
            pair=pair,
            client=client,
            strategy_config=strategy_config,
            db=self.db,
            dry_run=dry_run,
        )

        with self._lock:
            self.workers[bot_id] = worker

        # Start if status was RUNNING
        if bot_data.get('status') in ('RUNNING', 'STARTING'):
            worker.start()

        return worker

    def _get_strategy_config(self, strategy_id: Optional[str]) -> dict:
        """Get strategy configuration from database or return defaults"""
        if strategy_id:
            strategy = self.db.get_strategy(strategy_id)
            if strategy:
                return strategy

        # Return default strategy config
        return {
            'base_order_amount': 15000,
            'safety_order_amount': 15000,
            'max_safety_orders': 5,
            'price_deviation': 1.2,
            'deviation_scale': 1.5,
            'step_scale_enabled': False,
            'volume_scale': 1.5,
            'take_profit_percent': 1.0,
            'stop_loss_percent': 0.0,
            'martingale_enabled': False,
            'rsi_period': 14,
            'rsi_oversold': 60,
        }

    def start_bot(self, bot_id: str) -> bool:
        """Start a specific bot worker"""
        with self._lock:
            worker = self.workers.get(bot_id)
            if worker:
                worker.start()
                return True

        # Worker doesn't exist yet - create it
        bot_data = self.db.get_bot(bot_id)
        if not bot_data:
            self._logger.error(f"Bot {bot_id} not found")
            return False

        account_data = self.db.get_account(bot_data['account_id'])
        if not account_data:
            self._logger.error(f"Account {bot_data['account_id']} not found")
            return False

        from models.account import Account
        account = Account.from_dict(account_data)

        worker = self._start_single_worker(account, bot_data)
        return worker is not None

    def stop_bot(self, bot_id: str) -> bool:
        """Stop a specific bot worker"""
        with self._lock:
            worker = self.workers.get(bot_id)
            if worker:
                worker.stop()
                return True
        return False

    def pause_bot(self, bot_id: str) -> bool:
        """Pause a specific bot worker"""
        with self._lock:
            worker = self.workers.get(bot_id)
            if worker:
                worker.pause()
                return True
        return False

    def resume_bot(self, bot_id: str) -> bool:
        """Resume a paused bot worker"""
        with self._lock:
            worker = self.workers.get(bot_id)
            if worker:
                worker.resume()
                return True
        return False

    def add_bot_worker(self, account_id: str, bot_id: str,
                       pair: str, dry_run: bool = True,
                       strategy_id: Optional[str] = None) -> Optional[BotWorker]:
        """Add a new bot worker"""
        account_data = self.db.get_account(account_id)
        if not account_data:
            self._logger.error(f"Account {account_id} not found")
            return None

        from models.account import Account
        account = Account.from_dict(account_data)

        strategy_config = self._get_strategy_config(strategy_id)
        completed_dry_cycles = self.db.get_completed_dry_run_cycle_count(bot_id)
        if (not dry_run and not live_trading_allowed_for(
                bot_id, completed_dry_cycles, strategy_config)):
            self._block_live_worker(account.id, {
                'id': bot_id, 'status': 'STOPPED'
            })
            return None

        # Create and return worker without starting
        creds = self.account_service.get_decrypted_credentials(account.id)
        if not creds:
            self._raise_credential_alert(account.id, bot_id, 'worker tidak dibuat')
            return None
        self._resolve_credential_alert(account.id)

        client = IndodaxClient(creds['api_key'], creds['api_secret'])
        worker = BotWorker(
            account_id=account_id,
            bot_id=bot_id,
            pair=pair,
            client=client,
            strategy_config=strategy_config,
            db=self.db,
            dry_run=dry_run,
        )

        with self._lock:
            self.workers[bot_id] = worker

        return worker

    def _raise_credential_alert(self, account_id: str, bot_id: str,
                                consequence: str):
        try:
            self.db.raise_alert(
                kind='CREDENTIAL_DECRYPTION_FAILED',
                dedupe_key=f'credential-decryption:{account_id}',
                severity='CRITICAL',
                account_id=account_id,
                bot_id=bot_id,
                message=(f'Credential akun tidak dapat didekripsi; '
                         f'{consequence}'),
                metadata={'account_id': account_id, 'bot_id': bot_id},
            )
        except Exception as error:
            self._logger.error("Failed to persist credential alert: %s", error)

    def _resolve_credential_alert(self, account_id: str):
        try:
            self.db.resolve_alert(f'credential-decryption:{account_id}')
        except Exception as error:
            self._logger.error("Failed to resolve credential alert: %s", error)

    def _block_live_worker(self, account_id: str, bot_data: dict):
        bot_id = bot_data['id']
        self._logger.error(
            "Live worker %s blocked by production rollout gate", bot_id)
        stored_bot = self.db.get_bot(bot_id)
        if stored_bot and stored_bot.get('status') != 'STOPPED':
            stored_bot['status'] = 'STOPPED'
            self.db.update_bot(stored_bot)
        try:
            self.db.raise_alert(
                kind='LIVE_TRADING_BLOCKED',
                dedupe_key=f'live-gate:{bot_id}',
                severity='CRITICAL',
                account_id=account_id,
                bot_id=bot_id,
                message=(
                    'Live trading diblokir: aktifkan flag, konfirmasi risiko, '
                    'dan exposure cap sebelum menjalankan worker'
                ),
            )
        except Exception as error:
            self._logger.error("Failed to persist live gate alert: %s", error)

    def remove_bot_worker(self, bot_id: str):
        """Remove a bot worker (stop first if running)"""
        self.stop_bot(bot_id)
        with self._lock:
            if bot_id in self.workers:
                del self.workers[bot_id]

    def get_worker_status(self, bot_id: str) -> Optional[str]:
        """Get status of a specific worker"""
        with self._lock:
            worker = self.workers.get(bot_id)
            if worker:
                return worker.status.value
        return None

    def get_all_workers_status(self) -> list[dict]:
        """Get status of all workers"""
        statuses = []
        with self._lock:
            for bot_id, worker in self.workers.items():
                statuses.append({
                    'bot_id': bot_id,
                    'account_id': worker.account_id,
                    'pair': worker.pair,
                    'status': worker.status.value,
                    'dry_run': worker.dry_run,
                })
        return statuses

    def get_account_workers(self, account_id: str) -> list[BotWorker]:
        """Get all workers for a specific account"""
        workers = []
        with self._lock:
            for worker in self.workers.values():
                if worker.account_id == account_id:
                    workers.append(worker)
        return workers

    def refresh_account(self, account_id: str):
        """Refresh all workers for a given account (e.g. after credential update)"""
        # Stop all existing workers for this account
        existing = self.get_account_workers(account_id)
        for worker in existing:
            self.remove_bot_worker(worker.bot_id)

        # Reload from database
        account_data = self.db.get_account(account_id)
        if account_data:
            from models.account import Account
            account = Account.from_dict(account_data)
            self._load_account_workers(account)

    def shutdown_all(self):
        """Stop all workers gracefully"""
        self._logger.info("Shutting down all workers...")
        with self._lock:
            bot_ids = list(self.workers.keys())

        for bot_id in bot_ids:
            self.stop_bot(bot_id)

        self._logger.info("All workers stopped")

    def get_health_status(self) -> dict:
        """Get overall system health status"""
        total_workers = 0
        running = 0
        paused = 0
        error = 0
        stopped = 0

        with self._lock:
            for worker in self.workers.values():
                total_workers += 1
                if worker.status == BotStatus.RUNNING:
                    running += 1
                elif worker.status == BotStatus.PAUSED:
                    paused += 1
                elif worker.status == BotStatus.ERROR:
                    error += 1
                elif worker.status == BotStatus.STOPPED:
                    stopped += 1

        return {
            'total_workers': total_workers,
            'running': running,
            'paused': paused,
            'error': error,
            'stopped': stopped,
        }
