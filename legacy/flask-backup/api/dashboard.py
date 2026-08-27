"""
Dashboard API - Multi-Account DCA Bot
Flask routes untuk mengelola akun, bot, strategi, dan monitoring
"""
from flask import Flask, render_template, jsonify, request, session, redirect, url_for
from datetime import datetime
from functools import wraps

from database.database import DatabaseManager
from services.encryption_service import EncryptionService
from services.account_service import AccountService
from services.auth_service import AuthService
from core.bot_manager import BotManager
from config.constants import BotStatus



from typing import Optional

def create_app(db: DatabaseManager, encryption: EncryptionService,
               account_service: AccountService, bot_manager: BotManager,
               auth_service: AuthService) -> Flask:
    app = Flask(__name__, 
                template_folder='../templates',
                static_folder='../dashboard')

    app.secret_key = 'exbot-secret-key-change-in-production'

    def login_required(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated_function

    # ============================================================
    # Helper Functions
    # ============================================================
    def json_success(data=None, message=None):
        result = {'success': True}
        if data is not None:
            result['data'] = data
        if message:
            result['message'] = message
        return jsonify(result)

    def json_error(message, code=400):
        return jsonify({'success': False, 'error': message}), code

    # ============================================================
    # Dashboard Pages
    # ============================================================
    # ============================================================
    # Authentication Routes
    # ============================================================
    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if 'user_id' in session:
            return redirect(url_for('index'))
        error = None
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            result = auth_service.login(username, password)
            if result['success']:
                session['user_id'] = result['user_id']
                session['username'] = result['username']
                return redirect(url_for('index'))
            else:
                error = result['error']
        return render_template('login.html', error=error)

    @app.route('/register', methods=['GET', 'POST'])
    def register():
        if 'user_id' in session:
            return redirect(url_for('index'))
        error = None
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            confirm = request.form.get('confirm_password', '')
            if password != confirm:
                error = 'Password tidak cocok'
            else:
                result = auth_service.register(username, password)
                if result['success']:
                    return redirect(url_for('login'))
                else:
                    error = result['error']
        return render_template('register.html', error=error)

    @app.route('/logout')
    def logout():
        session.clear()
        return redirect(url_for('login'))

    # ============================================================
    # Dashboard Pages
    # ============================================================
    @app.route('/')
    @login_required
    def index():
        return render_template('index.html',
            bot_data={},
            current_price=0,
            idr_balance=0,
            crypto_balance=0,
            all_crypto_balances=[],
            total_crypto_value=0,
            total_portfolio_value=0,
            next_trade=None,
            avg_price=0,
            profit_loss=0,
            bot_running=False,
            current_config={
                'trading_pair': 'btcidr',
                'dry_run': True,
                'dca_interval_hours': 24,
                'dca_amount_idr': 10000,
            },
        )

    @app.route('/settings', methods=['GET', 'POST'])
    def settings_page():
        if request.method == 'POST':
            # Update the first account credentials if provided
            accounts = account_service.get_active_accounts()
            if accounts:
                acc = accounts[0]
                api_key = request.form.get('api_key', '').strip()
                secret_key = request.form.get('secret_key', '').strip()
                
                # Update API key if provided
                if api_key:
                    account_service.update_account(acc.id, api_key=api_key)
                
                # Update secret key only if provided (not empty)
                if secret_key and secret_key != '****':
                    account_service.update_account(acc.id, api_secret=secret_key)

            # Update the first strategy
            strategies = db.get_all_strategies()
            if strategies:
                strat = strategies[0]
                strat['base_order_amount'] = float(request.form.get('base_order_idr', 100000))
                strat['safety_order_amount'] = float(request.form.get('safety_order_idr', 50000))
                strat['max_safety_orders'] = int(request.form.get('max_safety_orders', 5))
                strat['price_deviation'] = float(request.form.get('safety_order_distance', 2.0))
                strat['take_profit_percent'] = float(request.form.get('take_profit_percent', 5.0))
                strat['stop_loss_percent'] = float(request.form.get('stop_loss_percent', 0.0))
                strat['martingale_enabled'] = 1 if request.form.get('martingle_enabled') == 'on' else 0
                strat['volume_scale'] = float(request.form.get('volume_scale', 1.5))
                strat['deviation_scale'] = float(request.form.get('step_scale', 1.2))
                strat['rsi_period'] = int(request.form.get('rsi_period', 14))
                strat['rsi_oversold'] = int(request.form.get('rsi_oversold', 30))
                db.update_strategy(strat)

            # Update the first bot
            bots = db.get_all_bots()
            if bots:
                bot = bots[0]
                bot['pair'] = request.form.get('trading_pair', 'btcidr')
                bot['dry_run'] = 1 if request.form.get('dry_run') == 'on' else 0
                db.update_bot(bot)
                
                # Refresh bot manager if account exists
                if accounts:
                    try:
                        bot_manager.refresh_account(accounts[0].id)
                    except Exception:
                        pass
                
            return redirect('/settings?success=1')
            
        return render_template('settings.html')

    @app.route('/trades')
    def trades_page():
        return render_template('trades.html')

    @app.route('/logs')
    def logs_page():
        return render_template('logs.html')

    # ============================================================
    # Account Management API
    # ============================================================
    @app.route('/api/accounts', methods=['GET'])
    def list_accounts():
        """Get all accounts"""
        accounts = account_service.get_all_accounts()
        return json_success([{
            'id': a.id,
            'name': a.name,
            'exchange': a.exchange,
            'is_active': a.is_active,
            'api_key_masked': EncryptionService.mask_credential(
                encryption.decrypt(a.api_key_encrypted)) if a.api_key_encrypted else '',
            'created_at': a.created_at,
            'last_connected_at': a.last_connected_at,
            'last_error': a.last_error,
            'bots_count': len(db.get_account_bots(a.id)),
        } for a in accounts])

    @app.route('/api/accounts', methods=['POST'])
    def create_account():
        """Create a new account"""
        data = request.get_json()
        if not data:
            return json_error('No data provided')

        name = data.get('name', '').strip()
        api_key = data.get('api_key', '').strip()
        api_secret = data.get('api_secret', '').strip()

        if not name:
            return json_error('Account name is required')
        if not api_key or not api_secret:
            return json_error('API key and secret are required')

        try:
            account = account_service.create_account(name, api_key, api_secret)
            return json_success({'id': account.id, 'name': account.name}, 'Account created')
        except Exception as e:
            return json_error(str(e))

    @app.route('/api/accounts/<account_id>', methods=['PUT'])
    def update_account(account_id):
        """Update an account"""
        data = request.get_json()
        if not data:
            return json_error('No data provided')

        account = account_service.update_account(
            account_id,
            name=data.get('name'),
            api_key=data.get('api_key'),
            api_secret=data.get('api_secret'),
            is_active=data.get('is_active'),
        )
        if not account:
            return json_error('Account not found', 404)

        # Refresh workers if account is updated
        if data.get('api_key') or data.get('api_secret'):
            bot_manager.refresh_account(account_id)

        return json_success({'id': account.id}, 'Account updated')

    @app.route('/api/accounts/<account_id>', methods=['DELETE'])
    def delete_account(account_id):
        """Delete an account"""
        # Stop all bots for this account first
        for worker in bot_manager.get_account_workers(account_id):
            bot_manager.remove_bot_worker(worker.bot_id)

        account_service.delete_account(account_id)
        return json_success(None, 'Account deleted')

    @app.route('/api/accounts/<account_id>/test', methods=['POST'])
    def test_account_connection(account_id):
        """Test API connection"""
        result = account_service.test_connection(account_id)
        if result['success']:
            return json_success({
                'idr_balance': result['idr_balance'],
                'balances': result['balance'],
            }, 'Connection successful')
        return json_error(result['error'])

    @app.route('/api/accounts/<account_id>/balance', methods=['GET'])
    def get_account_balance(account_id):
        """Get account balance from exchange"""
        result = account_service.test_connection(account_id)
        if result['success']:
            return json_success(result['balance'])
        return json_error(result['error'])

    @app.route('/api/accounts/<account_id>/toggle', methods=['POST'])
    def toggle_account(account_id):
        """Enable or disable an account"""
        data = request.get_json()
        is_active = data.get('is_active', True) if data else True

        account = account_service.update_account(account_id, is_active=is_active)
        if not account:
            return json_error('Account not found', 404)

        if not is_active:
            # Stop all bots for this account
            for worker in bot_manager.get_account_workers(account_id):
                bot_manager.stop_bot(worker.bot_id)
        else:
            # Reload account workers
            bot_manager.refresh_account(account_id)

        return json_success(None, f'Account {"enabled" if is_active else "disabled"}')

    # ============================================================
    # Bot Management API
    # ============================================================
    @app.route('/api/bots', methods=['GET'])
    def list_bots():
        """Get all bots"""
        bots = db.get_all_bots()
        return json_success([{
            'id': b['id'],
            'account_id': b['account_id'],
            'name': b['name'],
            'exchange': b['exchange'],
            'pair': b['pair'],
            'status': b['status'],
            'dry_run': bool(b['dry_run']),
            'strategy_id': b['strategy_id'],
            'created_at': b['created_at'],
            'worker_status': bot_manager.get_worker_status(b['id']),
        } for b in bots])

    @app.route('/api/bots', methods=['POST'])
    def create_bot():
        """Create a new bot"""
        data = request.get_json()
        if not data:
            return json_error('No data provided')

        account_id = data.get('account_id', '').strip()
        name = data.get('name', '').strip()
        pair = data.get('pair', 'btcidr').strip()
        strategy_id = data.get('strategy_id')
        dry_run = data.get('dry_run', True)

        if not account_id:
            return json_error('Account ID is required')
        if not name:
            return json_error('Bot name is required')

        # Check account exists
        account = account_service.get_account(account_id)
        if not account:
            return json_error('Account not found', 404)

        from models.bot import Bot
        bot = Bot(
            account_id=account_id,
            name=name,
            pair=pair,
            dry_run=dry_run,
            strategy_id=strategy_id,
        )
        db.add_bot(bot.to_dict())

        # Add worker
        bot_manager.add_bot_worker(account_id, bot.id, pair, dry_run, strategy_id)

        return json_success({'id': bot.id, 'name': bot.name}, 'Bot created')

    @app.route('/api/bots/<bot_id>', methods=['PUT'])
    def update_bot(bot_id):
        """Update a bot"""
        data = request.get_json()
        if not data:
            return json_error('No data provided')

        bot_data = db.get_bot(bot_id)
        if not bot_data:
            return json_error('Bot not found', 404)

        if 'name' in data:
            bot_data['name'] = data['name']
        if 'pair' in data:
            bot_data['pair'] = data['pair']
        if 'dry_run' in data:
            bot_data['dry_run'] = bool(data['dry_run'])
        if 'strategy_id' in data:
            bot_data['strategy_id'] = data['strategy_id']

        db.update_bot(bot_data)

        # Refresh worker
        bot_manager.remove_bot_worker(bot_id)
        account = account_service.get_account(bot_data['account_id'])
        if account:
            bot_manager.add_bot_worker(
                account.id, bot_id, bot_data['pair'],
                bot_data['dry_run'], bot_data['strategy_id']
            )

        return json_success(None, 'Bot updated')

    @app.route('/api/bots/<bot_id>', methods=['DELETE'])
    def delete_bot(bot_id):
        """Delete a bot"""
        bot_manager.remove_bot_worker(bot_id)
        db.delete_bot(bot_id)
        return json_success(None, 'Bot deleted')

    @app.route('/api/bots/<bot_id>/start', methods=['POST'])
    def start_bot(bot_id):
        """Start a bot"""
        if bot_manager.start_bot(bot_id):
            bot_data = db.get_bot(bot_id)
            if bot_data:
                bot_data['status'] = 'RUNNING'
                db.update_bot(bot_data)
            return json_success(None, 'Bot started')
        return json_error('Failed to start bot')

    @app.route('/api/bots/<bot_id>/stop', methods=['POST'])
    def stop_bot_api(bot_id):
        """Stop a bot"""
        if bot_manager.stop_bot(bot_id):
            bot_data = db.get_bot(bot_id)
            if bot_data:
                bot_data['status'] = 'STOPPED'
                db.update_bot(bot_data)
            return json_success(None, 'Bot stopped')
        return json_error('Failed to stop bot')

    @app.route('/api/bots/<bot_id>/pause', methods=['POST'])
    def pause_bot_api(bot_id):
        """Pause a bot"""
        if bot_manager.pause_bot(bot_id):
            return json_success(None, 'Bot paused')
        return json_error('Failed to pause bot')

    @app.route('/api/bots/<bot_id>/resume', methods=['POST'])
    def resume_bot_api(bot_id):
        """Resume a bot"""
        if bot_manager.resume_bot(bot_id):
            return json_success(None, 'Bot resumed')
        return json_error('Failed to resume bot')

    # ============================================================
    # Simple Bot Control API (for legacy frontend compatibility)
    # ============================================================
    @app.route('/api/start', methods=['POST'])
    def start_bot_simple():
        """Start the first available bot"""
        bots = db.get_all_bots()
        if not bots:
            return json_error('No bots configured')
        
        bot = bots[0]
        if bot_manager.start_bot(bot['id']):
            bot_data = db.get_bot(bot['id'])
            if bot_data:
                bot_data['status'] = 'RUNNING'
                db.update_bot(bot_data)
            return json_success(None, 'Bot started')
        return json_error('Failed to start bot')

    @app.route('/api/stop', methods=['POST'])
    def stop_bot_simple():
        """Stop the first available bot"""
        bots = db.get_all_bots()
        if not bots:
            return json_error('No bots configured')
        
        bot = bots[0]
        if bot_manager.stop_bot(bot['id']):
            bot_data = db.get_bot(bot['id'])
            if bot_data:
                bot_data['status'] = 'STOPPED'
                db.update_bot(bot_data)
            return json_success(None, 'Bot stopped')
        return json_error('Failed to stop bot')

    # ============================================================
    # Strategy Management API
    # ============================================================
    @app.route('/api/strategies', methods=['GET'])
    def list_strategies():
        """Get all strategies"""
        strategies = db.get_all_strategies()
        return json_success(strategies)

    @app.route('/api/strategies', methods=['POST'])
    def create_strategy():
        """Create a new strategy"""
        data = request.get_json()
        if not data:
            return json_error('No data provided')

        from models.strategy import Strategy
        strategy = Strategy(
            name=data.get('name', 'New Strategy'),
            base_order_amount=float(data.get('base_order_amount', 15000)),
            safety_order_amount=float(data.get('safety_order_amount', 15000)),
            max_safety_orders=int(data.get('max_safety_orders', 5)),
            price_deviation=float(data.get('price_deviation', 1.2)),
            deviation_scale=float(data.get('deviation_scale', 1.5)),
            volume_scale=float(data.get('volume_scale', 1.5)),
            take_profit_percent=float(data.get('take_profit_percent', 1.0)),
            stop_loss_percent=float(data.get('stop_loss_percent', 0.0)),
            martingale_enabled=bool(data.get('martingale_enabled', False)),
            rsi_period=int(data.get('rsi_period', 14)),
            rsi_oversold=int(data.get('rsi_oversold', 60)),
            initial_entry_mode=str(data.get('initial_entry_mode', 'MARKET')).upper(),
        )
        db.add_strategy(strategy.to_dict())
        return json_success({'id': strategy.id}, 'Strategy created')

    @app.route('/api/strategies/<strategy_id>', methods=['PUT'])
    def update_strategy(strategy_id):
        """Update a strategy"""
        data = request.get_json()
        if not data:
            return json_error('No data provided')

        strategy_data = db.get_strategy(strategy_id)
        if not strategy_data:
            return json_error('Strategy not found', 404)

        for key in ['name', 'base_order_amount', 'safety_order_amount',
                     'max_safety_orders', 'price_deviation', 'deviation_scale',
                     'volume_scale', 'take_profit_percent', 'stop_loss_percent',
                     'martingale_enabled', 'rsi_period', 'rsi_oversold', 'initial_entry_mode']:
            if key in data:
                strategy_data[key] = data[key]

        db.update_strategy(strategy_data)
        return json_success(None, 'Strategy updated')

    @app.route('/api/strategies/<strategy_id>', methods=['DELETE'])
    def delete_strategy(strategy_id):
        """Delete a strategy"""
        db.delete_strategy(strategy_id)
        return json_success(None, 'Strategy deleted')

    # ============================================================
    # Bot Status & Monitoring API
    # ============================================================
    @app.route('/api/status', methods=['GET'])
    def system_status():
        """Get complete system status"""
        workers_status = bot_manager.get_all_workers_status()
        health = bot_manager.get_health_status()
        accounts = account_service.get_all_accounts()

        # Get first active account credentials for settings page
        api_key = ''
        secret_key_masked = ''
        active_accounts = [a for a in accounts if a.is_active]
        if active_accounts:
            acc = active_accounts[0]
            if acc.api_key_encrypted:
                try:
                    api_key = encryption.decrypt(acc.api_key_encrypted)
                    secret_key_masked = EncryptionService.mask_credential(
                        encryption.decrypt(acc.api_secret_encrypted)
                    ) if acc.api_secret_encrypted else ''
                except Exception:
                    pass

        return json_success({
            'workers': workers_status,
            'health': health,
            'total_accounts': len(accounts),
            'active_accounts': len(active_accounts),
            'api_key': api_key,
            'secret_key_masked': secret_key_masked,
        })

    @app.route('/api/health', methods=['GET'])
    def health_status():
        """Get bot manager health status"""
        return json_success(bot_manager.get_health_status())

    @app.route('/api/bots/<bot_id>/position', methods=['GET'])
    def get_bot_position(bot_id):
        """Get current position for a bot"""
        position = db.get_position(bot_id)
        if position:
            return json_success(position)
        return json_success(None)

    @app.route('/api/bots/<bot_id>/orders', methods=['GET'])
    def get_bot_orders(bot_id):
        """Get orders for a bot"""
        orders = db.get_bot_orders(bot_id)
        return json_success(orders)

    @app.route('/api/bots/<bot_id>/logs', methods=['GET'])
    def get_bot_logs(bot_id):
        """Get logs for a bot"""
        account_id = request.args.get('account_id')
        limit = int(request.args.get('limit', 100))
        logs = db.get_logs(account_id=account_id, bot_id=bot_id, limit=limit)
        return json_success(logs)

    # ============================================================
    # Logs API
    # ============================================================
    @app.route('/api/logs', methods=['GET'])
    def get_logs():
        """Get logs with optional filters"""
        account_id = request.args.get('account_id')
        bot_id = request.args.get('bot_id')
        level = request.args.get('level')
        limit = int(request.args.get('limit', 100))

        logs = db.get_logs(
            account_id=account_id,
            bot_id=bot_id,
            level=level,
            limit=limit
        )
        return json_success(logs)

    # ============================================================
    # Exchange Data API
    # ============================================================
    @app.route('/api/ticker')
    def get_ticker():
        """Get ticker for a specific pair (uses first available account)"""
        pair = request.args.get('pair', 'btcidr')
        accounts = account_service.get_active_accounts()

        if not accounts:
            return json_error('No active accounts configured')

        try:
            creds = account_service.get_decrypted_credentials(accounts[0].id)
            if not creds:
                return json_error('Failed to decrypt credentials')

            from exchanges.indodax_client import IndodaxClient
            client = IndodaxClient(creds['api_key'], creds['api_secret'])
            ticker = client.get_ticker(pair)
            return json_success(ticker)
        except Exception as e:
            return json_error(str(e))

    @app.route('/api/account-ticker/<account_id>')
    def get_account_ticker(account_id):
        """Get ticker using a specific account"""
        pair = request.args.get('pair', 'btcidr')
        creds = account_service.get_decrypted_credentials(account_id)

        if not creds:
            return json_error('Failed to decrypt credentials')

        from exchanges.indodax_client import IndodaxClient
        client = IndodaxClient(creds['api_key'], creds['api_secret'])
        ticker = client.get_ticker(pair)
        return json_success(ticker)

    # ============================================================
    # Migration API (Legacy dca_data.json)
    # ============================================================
    @app.route('/api/migrate', methods=['POST'])
    def migrate_legacy_data():
        """Migrate data from legacy dca_data.json to database"""
        import json
        import os

        legacy_file = request.args.get('file', 'dca_data.json')
        if not os.path.exists(legacy_file):
            return json_error(f'File {legacy_file} not found')

        try:
            with open(legacy_file, 'r') as f:
                legacy_data = json.load(f)
        except Exception as e:
            return json_error(f'Failed to read legacy file: {e}')

        # Create a legacy account
        account = account_service.create_account(
            name="Legacy Account",
            api_key="",
            api_secret="",
        )

        # Create a legacy bot
        from models.bot import Bot
        bot = Bot(
            account_id=account.id,
            name=f"Legacy {legacy_data.get('pair', 'btcidr').upper()}",
            pair=legacy_data.get('pair', 'btcidr'),
            dry_run=True,
        )
        db.add_bot(bot.to_dict())

        # Create position from legacy state
        if legacy_data.get('active_position'):
            position = {
                'bot_id': bot.id,
                'status': 'OPEN',
                'base_price': legacy_data.get('base_price', 0),
                'base_amount': legacy_data.get('base_amount_crypto', 0),
                'total_amount': legacy_data.get('total_crypto_bought', 0),
                'total_invested': legacy_data.get('total_invested', 0),
                'take_profit_price': legacy_data.get('tp_price', 0),
                'stop_loss_price': legacy_data.get('sl_price', 0),
                'so_entries': legacy_data.get('so_entries', []),
                'tp_order_id': legacy_data.get('tp_order_id'),
                'open_orders': legacy_data.get('open_orders', []),
            }
            import uuid
            position['id'] = f"pos_{uuid.uuid4().hex[:12]}"
            db.save_position(position)

        # Import trades
        for trade in legacy_data.get('trades', []):
            db.add_order({
                'bot_id': bot.id,
                'account_id': account.id,
                'exchange_order_id': trade.get('order_id', ''),
                'order_type': 'buy',
                'side': trade.get('type', 'base'),
                'pair': bot.pair,
                'price': float(trade.get('price', 0)),
                'amount': float(trade.get('amount_crypto', 0)),
                'amount_quote': float(trade.get('amount_idr', 0)),
                'status': 'FILLED',
                'is_dca': trade.get('type', '') != 'take_profit',
                'dca_level': 0,
                'so_number': 0,
            })

        return json_success({
            'account_id': account.id,
            'bot_id': bot.id,
            'trades_imported': len(legacy_data.get('trades', [])),
        }, 'Migration completed')
    # ============================================================
    # Legacy UI Support APIs
    # ============================================================
    @app.route('/api/candlestick')
    def api_candlestick():
        timeframe = request.args.get('timeframe', '1h')
        limit = int(request.args.get('limit', '100'))
        accounts = account_service.get_active_accounts()
        if not accounts: return jsonify({'success': False, 'error': 'No active accounts'})
        creds = account_service.get_decrypted_credentials(accounts[0].id)
        bots = db.get_all_bots()
        pair = bots[0]['pair'] if bots else 'btcidr'
        from exchanges.indodax_client import IndodaxClient
        client = IndodaxClient(creds['api_key'], creds['api_secret'])
        candles = client.get_ohlc(pair, timeframe, limit)
        if isinstance(candles, list):
            return jsonify({'success': True, 'data': candles, 'pair': pair, 'timeframe': timeframe})
        return jsonify({'success': False, 'error': candles.get('error', 'Failed') if isinstance(candles, dict) else 'Failed'})

    @app.route('/api/trades')
    def api_trades():
        bots = db.get_all_bots()
        if not bots: return jsonify({'success': True, 'data': []})
        orders = db.get_bot_orders(bots[0]['id'], limit=50)
        formatted = []
        for o in orders:
            formatted.append({
                'timestamp': o['created_at'],
                'price': float(o['price']),
                'amount_idr': float(o['amount_quote']),
                'crypto_amount': float(o['amount']),
                'order_id': o['exchange_order_id'],
                'type': o['side'],
                'dry_run': False
            })
        return jsonify({'success': True, 'data': formatted})

    @app.route('/api/exchange-trades')
    def api_exchange_trades():
        accounts = account_service.get_active_accounts()
        if not accounts: return jsonify({'success': False, 'error': 'No active accounts'})
        creds = account_service.get_decrypted_credentials(accounts[0].id)
        bots = db.get_all_bots()
        pair = bots[0]['pair'] if bots else 'btcidr'
        from exchanges.indodax_client import IndodaxClient
        client = IndodaxClient(creds['api_key'], creds['api_secret'])
        trades = client.get_trade_history(pair, limit=50)
        if isinstance(trades, dict) and 'error' in trades:
            return jsonify({'success': False, 'error': trades['error']})
        formatted = []
        if isinstance(trades, list):
            coin = pair.lower().replace('_', '').replace('idr', '')
            for trade in trades:
                try:
                    price = float(trade.get('price', 0))
                    crypto_amount = float(trade.get(coin, trade.get('amount', 0)))
                    trade_time = int(trade.get('trade_time', 0))
                    formatted_time = datetime.fromtimestamp(trade_time).strftime('%Y-%m-%d %H:%M:%S') if trade_time else ''
                    formatted.append({
                        'order_id': trade.get('order_id', ''),
                        'type': trade.get('type', ''),
                        'price': price,
                        'amount': price * crypto_amount,
                        'amount_crypto': crypto_amount,
                        'time': formatted_time,
                        'submit_time': formatted_time,
                        'status': 'filled',
                        'fee': float(trade.get('fee', 0)) if trade.get('fee') else 0
                    })
                except (ValueError, TypeError): continue
        return jsonify({'success': True, 'data': formatted})

    @app.route('/api/open-orders')
    def api_open_orders():
        accounts = account_service.get_active_accounts()
        if not accounts: return jsonify({'success': False, 'error': 'No active accounts'})
        creds = account_service.get_decrypted_credentials(accounts[0].id)
        bots = db.get_all_bots()
        pair = bots[0]['pair'] if bots else 'btcidr'
        from exchanges.indodax_client import IndodaxClient
        client = IndodaxClient(creds['api_key'], creds['api_secret'])
        api_pair = pair.lower()
        if '_' not in api_pair and 'idr' in api_pair:
            idx = api_pair.find('idr')
            api_pair = api_pair[:idx] + '_' + api_pair[idx:]
        orders = client.get_open_orders(api_pair)
        if isinstance(orders, dict) and 'error' in orders:
            orders = client.get_open_orders(pair)
        if isinstance(orders, dict) and 'error' in orders:
            return jsonify({'success': False, 'error': orders['error']})
        formatted = []
        if isinstance(orders, list):
            for order in orders:
                try:
                    submit = order.get('submit_time', 0)
                    time_str = datetime.fromtimestamp(int(submit)).strftime('%Y-%m-%d %H:%M:%S') if submit else ''
                    formatted.append({
                        'order_id': str(order.get('order_id', '')),
                        'type': str(order.get('type', '')),
                        'price': float(order.get('price', 0)),
                        'amount': float(order.get('amount', 0)),
                        'amount_remaining': float(order.get('amount_remaining', 0)),
                        'time': time_str
                    })
                except (ValueError, TypeError): continue
        return jsonify({'success': True, 'data': formatted})

    return app