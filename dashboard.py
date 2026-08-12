"""
Exbot DCA Bot - Web Dashboard
Dashboard web untuk monitoring dan konfigurasi bot DCA
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
import json
import os
import threading
import time
import importlib
from datetime import datetime, timedelta
from indodax_client import IndodaxClient
import config

app = Flask(__name__)

# Global variables for bot state
bot_state = {
    'running': False,
    'last_update': None,
    'bot_data': {}
}

def load_bot_data():
    """Load bot data from JSON file"""
    try:
        with open(config.DATA_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            'last_trade': None,
            'next_trade': None,
            'total_trades': 0,
            'total_invested': 0,
            'total_crypto_bought': 0,
            'trades': []
        }

def save_bot_data(data):
    """Save bot data to JSON file"""
    with open(config.DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# Crypto icon mapping for display
CRYPTO_ICONS = {
    'btc': '₿',
    'eth': 'Ξ',
    'bch': 'Ƀ',
    'doge': 'Ð',
    'xrp': '✕',
    'idr': 'Rp',
    'usdt': '₮',
    'bnb': 'BNB',
    'sol': '◎',
    'ada': '₳',
    'dot': 'DOT',
    'matic': 'MATIC',
    'link': 'LINK',
    'uni': '🦄',
    'avax': 'AVAX',
    'ltc': 'Ł',
    'xlm': '*',
    'trx': 'TRX',
    'etc': 'Ξ',
    'atom': 'ATOM',
    'fil': '📁',
    'aave': 'A',
    'sushi': '🍣',
    'comp': 'C',
    'mkr': 'MKR',
    'snx': 'SNX',
    'yfi': 'YFI',
    '1inch': '📐',
    'crv': 'CRV',
    'bat': '🦇',
    'zec': 'Z',
    'dash': 'D',
    'xmr': 'MR',
    'neo': 'NAS',
    'ont': 'ONT',
    'vet': 'V',
    'icx': 'ICX',
    'qtum': 'QTUM',
    'lsk': 'LSK',
    'waves': 'WAVES',
    'strat': 'STRAT',
    'ark': 'ARK',
    'kmd': 'KMD',
    'dgb': 'DGB',
    'rvn': 'RVN',
    'nano': 'NANO',
    'sc': 'SC',
    'dcr': 'DCR',
    'zen': 'ZEN',
    'xem': 'NEM',
    'hbar': 'HBAR',
    'algo': 'ALGO',
    'egld': 'eGLD',
    'near': 'NEAR',
    'flow': 'FLOW',
    'icp': 'ICP',
    'theta': 'Θ',
    'tfuel': 'TF',
    'klay': 'KLAY',
    'celo': 'CELO',
    'one': 'ONE',
    'harmony': 'ONE',
    'ftm': 'Fantom',
    'cro': 'CRO',
    'ht': 'HT',
    'okb': 'OKB',
    'leo': 'LEO',
    'tusd': 'TUSD',
    'busd': 'BUSD',
    'usdc': 'USDC',
    'dai': 'DAI',
    'pax': 'PAX',
    'gusd': 'GUSD',
    'husd': 'HUSD',
    'susd': 'SUSD',
    'eurs': 'EURS',
    'eurt': 'EURT',
    'xaut': 'XAUT',
    'paxg': 'PAXG',
    'pmgt': 'PMGT',
}

def get_crypto_icon(symbol):
    """Get icon for crypto symbol"""
    symbol_lower = symbol.lower()
    return CRYPTO_ICONS.get(symbol_lower, symbol.upper()[:2])

def get_all_balances(balance_data):
    """Extract all crypto balances from balance data with current prices"""
    if 'error' in balance_data or 'balance' not in balance_data:
        print(f"[DEBUG] get_all_balances: Invalid balance data - error={balance_data.get('error', 'N/A')}")
        return []
    
    balances = []
    balance_dict = balance_data.get('balance', {})
    
    print(f"[DEBUG] get_all_balances: Found {len(balance_dict)} items in balance dict")
    
    # Get all ticker prices at once for efficiency
    client = IndodaxClient(config.INDODAX_API_KEY, config.INDODAX_SECRET_KEY)
    all_tickers = client.get_ticker_all()
    
    print(f"[DEBUG] get_all_balances: get_ticker_all returned: {type(all_tickers).__name__}")
    if 'error' in all_tickers:
        print(f"[DEBUG] get_all_balances: ticker_all error: {all_tickers['error']}")
    
    # Build a price lookup dictionary from tickers
    # get_ticker_all returns dict like: {"tickers": {"btcidr": {"last": "..."}, "ethidr": {"last": "..."}, ...}}
    price_map = {}
    if 'error' not in all_tickers and 'tickers' in all_tickers and isinstance(all_tickers['tickers'], dict):
        tickers_count = len(all_tickers['tickers'])
        print(f"[DEBUG] get_all_balances: Got {tickers_count} tickers")
        for pair, ticker_data in all_tickers['tickers'].items():
            if isinstance(ticker_data, dict) and 'last' in ticker_data:
                try:
                    price_map[pair.lower()] = float(ticker_data['last'])
                except (ValueError, TypeError):
                    pass
        print(f"[DEBUG] get_all_balances: price_map has {len(price_map)} entries")
    elif 'error' not in all_tickers:
        print(f"[DEBUG] get_all_balances: No 'tickers' key or 'tickers' is not a dict in response, keys: {list(all_tickers.keys()) if isinstance(all_tickers, dict) else 'not a dict'}")
    
    for symbol, amount in balance_dict.items():
        # Skip zero balances and idr (handled separately)
        if symbol == 'idr' or float(amount) == 0:
            continue
        
        amount_float = float(amount)
        if amount_float < 0.00000001:  # Skip very small amounts
            continue
        
        icon = get_crypto_icon(symbol)
        
        # Get current price for this crypto from our price map
        pair = symbol + 'idr'
        price = price_map.get(pair.lower(), 0)
        
        if price == 0:
            print(f"[DEBUG] get_all_balances: No price found for {pair} (looking for key: {pair.lower()})")
            # Try to find a matching key in price_map
            matching_keys = [k for k in price_map.keys() if symbol in k]
            if matching_keys:
                print(f"[DEBUG] get_all_balances: Found similar keys: {matching_keys}")
        else:
            print(f"[DEBUG] get_all_balances: Price for {pair}: {price}")
        
        value_idr = amount_float * price if price > 0 else 0
        
        balances.append({
            'symbol': symbol.upper(),
            'icon': icon,
            'amount': amount_float,
            'price': price,
            'value_idr': value_idr,
            'pair': pair
        })
    
    # Sort by value (highest first)
    balances.sort(key=lambda x: x['value_idr'], reverse=True)
    
    print(f"[DEBUG] get_all_balances: Returning {len(balances)} balances")
    return balances


def load_config():
    """Load current config"""
    return {
        'api_key': config.INDODAX_API_KEY,
        'secret_key': config.INDODAX_SECRET_KEY,
        'dca_interval_hours': config.DCA_INTERVAL_HOURS,
        'dca_amount_idr': config.DCA_AMOUNT_IDR,
        'trading_pair': config.TRADING_PAIR,
        'dry_run': config.DRY_RUN
    }

def save_config(config_data):
    """Save config to file - update hanya nilai yang diubah"""
    # Read existing config.py content
    with open('config.py', 'r') as f:
        lines = f.readlines()
    
    # Update specific values
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith('INDODAX_API_KEY ='):
            new_lines.append(f"INDODAX_API_KEY = '{config_data['api_key']}'\n")
        elif stripped.startswith('INDODAX_SECRET_KEY ='):
            new_lines.append(f"INDODAX_SECRET_KEY = '{config_data['secret_key']}'\n")
        elif stripped.startswith('DCA_INTERVAL_HOURS ='):
            new_lines.append(f"DCA_INTERVAL_HOURS = {config_data['dca_interval_hours']}  # Interval pembelian (jam)\n")
        elif stripped.startswith('DCA_AMOUNT_IDR ='):
            new_lines.append(f"DCA_AMOUNT_IDR = {config_data['dca_amount_idr']}  # Jumlah pembelian per interval (IDR)\n")
        elif stripped.startswith('TRADING_PAIR ='):
            new_lines.append(f"TRADING_PAIR = '{config_data['trading_pair']}'  # Pair trading (btcidr, ethidr, dll)\n")
        elif stripped.startswith('DRY_RUN ='):
            new_lines.append(f"DRY_RUN = {config_data['dry_run']}  # Mode simulasi (True = tidak benar-benar trading)\n")
        else:
            new_lines.append(line)
    
    with open('config.py', 'w') as f:
        f.writelines(new_lines)
    
    # Reload config module
    importlib.reload(config)

@app.route('/')
def index():
    """Dashboard utama"""
    bot_data = load_bot_data()
    current_config = load_config()
    
    # Get current price
    client = IndodaxClient(config.INDODAX_API_KEY, config.INDODAX_SECRET_KEY)
    ticker = client.get_ticker(current_config['trading_pair'])
    current_price = float(ticker.get('last', 0)) if 'error' not in ticker else 0
    
    # Get balance
    balance = client.get_balance()
    idr_balance = 0
    crypto_balance = 0
    all_crypto_balances = []
    if isinstance(balance, dict) and 'error' not in balance:
        balance_data = balance.get('balance', {})
        if isinstance(balance_data, dict):
            idr_balance = float(balance_data.get('idr', 0))
            crypto_symbol = current_config['trading_pair'].replace('idr', '')
            crypto_balance = float(balance_data.get(crypto_symbol, 0))
        # Get all crypto balances
        all_crypto_balances = get_all_balances(balance)
    
    # Calculate next trade time
    next_trade = None
    if bot_data.get('last_trade'):
        last_trade = datetime.fromisoformat(str(bot_data['last_trade']))
        next_trade = last_trade + timedelta(hours=current_config['dca_interval_hours'])
        next_trade = next_trade.strftime('%Y-%m-%d %H:%M:%S')
    
    # Calculate average price and P/L
    avg_price = 0.0
    profit_loss = 0.0
    profit_loss_idr = 0.0
    total_crypto_bought = float(bot_data.get('total_crypto_bought', 0) or 0)
    total_invested = float(bot_data.get('total_invested', 0) or 0)
    if total_crypto_bought > 0:
        avg_price = total_invested / total_crypto_bought
        if current_price > 0:
            fee_percent = getattr(config, 'MARKET_SELL_FEE_PERCENT', 0.30)
            net_value = total_crypto_bought * current_price * (1 - fee_percent / 100)
            profit_loss_idr = net_value - total_invested
            profit_loss = (profit_loss_idr / total_invested * 100) if total_invested > 0 else 0.0
    
    # Calculate total portfolio value
    total_crypto_value = sum(b['value_idr'] for b in all_crypto_balances)
    total_portfolio_value = idr_balance + total_crypto_value
    
    return render_template('index.html',
        bot_data=bot_data,
        current_config=current_config,
        current_price=current_price,
        idr_balance=idr_balance,
        crypto_balance=crypto_balance,
        all_crypto_balances=all_crypto_balances,
        total_crypto_value=total_crypto_value,
        total_portfolio_value=total_portfolio_value,
        next_trade=next_trade,
        avg_price=avg_price,
        profit_loss=profit_loss,
        profit_loss_idr=profit_loss_idr,
        bot_running=bot_state['running']
    )

@app.route('/api/status')
def api_status():
    """API endpoint for bot status"""
    bot_data = load_bot_data()
    current_config = load_config()
    
    client = IndodaxClient(config.INDODAX_API_KEY, config.INDODAX_SECRET_KEY)
    ticker = client.get_ticker(current_config['trading_pair'])
    current_price = float(ticker.get('last', 0)) if 'error' not in ticker else 0
    
    balance = client.get_balance()
    idr_balance = 0
    crypto_balance = 0
    all_crypto_balances = []
    if isinstance(balance, dict) and 'error' not in balance:
        balance_data = balance.get('balance', {})
        if isinstance(balance_data, dict):
            idr_balance = float(balance_data.get('idr', 0))
            crypto_symbol = current_config['trading_pair'].replace('idr', '')
            crypto_balance = float(balance_data.get(crypto_symbol, 0))
        all_crypto_balances = get_all_balances(balance)
    
    next_trade = None
    if bot_data.get('last_trade'):
        last_trade = datetime.fromisoformat(str(bot_data['last_trade']))
        next_trade = last_trade + timedelta(hours=current_config['dca_interval_hours'])
        next_trade = next_trade.isoformat()
    
    avg_price = 0.0
    profit_loss = 0.0
    profit_loss_idr = 0.0
    total_crypto_bought = float(bot_data.get('total_crypto_bought', 0) or 0)
    total_invested = float(bot_data.get('total_invested', 0) or 0)
    if total_crypto_bought > 0:
        avg_price = total_invested / total_crypto_bought
        if current_price > 0:
            fee_percent = getattr(config, 'MARKET_SELL_FEE_PERCENT', 0.30)
            net_value = total_crypto_bought * current_price * (1 - fee_percent / 100)
            profit_loss_idr = net_value - total_invested
            profit_loss = (profit_loss_idr / total_invested * 100) if total_invested > 0 else 0.0
    
    total_crypto_value = sum(b['value_idr'] for b in all_crypto_balances)
    total_portfolio_value = idr_balance + total_crypto_value
    
    return jsonify({
        'success': True,
        'data': {
            'current_price': current_price,
            'idr_balance': idr_balance,
            'crypto_balance': crypto_balance,
            'all_crypto_balances': all_crypto_balances,
            'total_crypto_value': total_crypto_value,
            'total_portfolio_value': total_portfolio_value,
            'total_trades': bot_data.get('total_trades', 0),
            'total_invested': bot_data.get('total_invested', 0),
            'total_crypto_bought': bot_data.get('total_crypto_bought', 0),
            'avg_price': avg_price,
            'profit_loss': profit_loss,
            'profit_loss_idr': profit_loss_idr,
            'next_trade': next_trade,
            'last_trade': bot_data.get('last_trade'),
            'bot_running': bot_state['running'],
            'dry_run': current_config['dry_run'],
            'api_key': config.INDODAX_API_KEY,
            'secret_key_masked': config.INDODAX_SECRET_KEY[:6] + '*' * (len(config.INDODAX_SECRET_KEY) - 10) + config.INDODAX_SECRET_KEY[-4:] if len(config.INDODAX_SECRET_KEY) > 10 else '****'
        }
    })

@app.route('/api/balances')
def api_balances():
    """API endpoint for all balances"""
    client = IndodaxClient(config.INDODAX_API_KEY, config.INDODAX_SECRET_KEY)
    balance = client.get_balance()
    
    if not isinstance(balance, dict) or 'error' in balance:
        return jsonify({
            'success': False,
            'error': balance.get('error', 'Unknown error') if isinstance(balance, dict) else 'Invalid balance response'
        })
    
    balance_data = balance.get('balance', {})
    idr_balance = float(balance_data.get('idr', 0)) if isinstance(balance_data, dict) else 0
    all_crypto_balances = get_all_balances(balance)
    total_crypto_value = sum(b['value_idr'] for b in all_crypto_balances)
    total_portfolio_value = idr_balance + total_crypto_value
    
    return jsonify({
        'success': True,
        'data': {
            'idr_balance': idr_balance,
            'all_crypto_balances': all_crypto_balances,
            'total_crypto_value': total_crypto_value,
            'total_portfolio_value': total_portfolio_value
        }
    })


@app.route('/api/candlestick')
def api_candlestick():
    """API endpoint for candlestick chart data"""
    current_config = load_config()
    timeframe = request.args.get('timeframe', '1h')
    limit = int(request.args.get('limit', '100'))
    
    client = IndodaxClient(config.INDODAX_API_KEY, config.INDODAX_SECRET_KEY)
    candles = client.get_ohlc(current_config['trading_pair'], timeframe, limit)
    
    if isinstance(candles, list):
        return jsonify({
            'success': True,
            'data': candles,
            'pair': current_config['trading_pair'],
            'timeframe': timeframe
        })
    else:
        return jsonify({
            'success': False,
            'error': candles.get('error', 'Unknown error') if isinstance(candles, dict) else 'Failed to fetch candlestick data'
        })

@app.route('/api/trades')
def api_trades():
    """API endpoint for trade history"""
    bot_data = load_bot_data()
    trades = list(bot_data.get('trades', []) or [])
    
    # Return last 50 trades
    recent_trades = trades[-50:][::-1]
    
    return jsonify({
        'success': True,
        'data': recent_trades
    })

@app.route('/api/exchange-trades')
def api_exchange_trades():
    """API endpoint for trade history from Indodax exchange"""
    client = IndodaxClient(config.INDODAX_API_KEY, config.INDODAX_SECRET_KEY)
    trades = client.get_trade_history(config.TRADING_PAIR, limit=50)
    
    if isinstance(trades, dict) and 'error' in trades:
        return jsonify({
            'success': False,
            'error': trades['error']
        })
    
    formatted_trades = []
    if isinstance(trades, list):
        coin = config.TRADING_PAIR.lower().replace('_', '').replace('idr', '')
        for trade in trades:
            try:
                price = float(trade.get('price', 0))
                crypto_amount = float(trade.get(coin, trade.get('amount', 0)))
                idr_amount = price * crypto_amount
                trade_time = int(trade.get('trade_time', 0))
                formatted_time = datetime.fromtimestamp(trade_time).strftime('%Y-%m-%d %H:%M:%S') if trade_time else ''
                
                formatted_trades.append({
                    'order_id': trade.get('order_id', ''),
                    'type': trade.get('type', ''),
                    'price': price,
                    'amount': idr_amount,
                    'amount_crypto': crypto_amount,
                    'time': formatted_time,
                    'submit_time': formatted_time,
                    'status': 'filled',
                    'fee': float(trade.get('fee', 0)) if trade.get('fee') else 0
                })
            except (ValueError, TypeError):
                continue
    
    return jsonify({
        'success': True,
        'data': formatted_trades
    })

@app.route('/api/open-orders')
def api_open_orders():
    """API endpoint for open orders from Indodax"""
    try:
        client = IndodaxClient(config.INDODAX_API_KEY, config.INDODAX_SECRET_KEY)
        
        # Indodax API expects pair format like "btc_idr" for private API
        pair = config.TRADING_PAIR.lower()
        if '_' not in pair:
            # Convert "btcidr" to "btc_idr"
            idx = pair.find('idr')
            if idx > 0:
                pair = pair[:idx] + '_' + pair[idx:]
        
        orders = client.get_open_orders(pair)
        
        if isinstance(orders, dict) and 'error' in orders:
            # Try with original pair format as fallback
            orders = client.get_open_orders(config.TRADING_PAIR)
        
        if isinstance(orders, dict) and 'error' in orders:
            return jsonify({
                'success': False,
                'error': orders['error']
            })
        
        # Format orders
        formatted_orders = []
        if isinstance(orders, list):
            for order in orders:
                try:
                    price = float(order.get('price', 0))
                    amount = float(order.get('amount', 0))
                    remain = float(order.get('amount_remaining', 0))
                    submit = order.get('submit_time', 0)
                    time_str = ''
                    if submit:
                        try:
                            time_str = datetime.fromtimestamp(int(submit)).strftime('%Y-%m-%d %H:%M:%S')
                        except:
                            time_str = str(submit)
                    
                    formatted_orders.append({
                        'order_id': str(order.get('order_id', '')),
                        'type': str(order.get('type', '')),
                        'price': price,
                        'amount': amount,
                        'amount_remaining': remain,
                        'time': time_str
                    })
                except (ValueError, TypeError) as e:
                    continue
        
        return jsonify({
            'success': True,
            'data': formatted_orders
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        })

@app.route('/api/trade-markers')
def api_trade_markers():
    """API endpoint for trade markers on candlestick chart"""
    bot_data = load_bot_data()
    trades = list(bot_data.get('trades', []) or [])
    
    # Convert trades to markers format, treating all as 'buy' (DCA bot only buys)
    markers = []
    for trade in trades:
        try:
            timestamp = trade.get('timestamp', '')
            # Parse timestamp to Unix timestamp (seconds)
            if timestamp:
                dt = datetime.fromisoformat(timestamp)
                unix_time = dt.timestamp()
            else:
                continue
            
            price = float(trade.get('price', 0))
            if price <= 0:
                continue
            
            # Determine type - default to 'buy' for DCA, check if type field exists
            trade_type = trade.get('type', 'buy')
            
            markers.append({
                'timestamp': unix_time,
                'price': price,
                'type': trade_type,
                'amount_idr': float(trade.get('amount_idr', 0)),
                'crypto_amount': float(trade.get('crypto_amount', 0)),
                'dry_run': trade.get('dry_run', False)
            })
        except (ValueError, TypeError):
            continue
    
    return jsonify({
        'success': True,
        'data': markers
    })

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    """Settings page"""
    if request.method == 'POST':
        # If secret_key is empty, keep existing one (user didn't want to change)
        new_secret = request.form.get('secret_key', '')
        if not new_secret.strip():
            new_secret = config.INDODAX_SECRET_KEY
        
        config_data = {
            'api_key': request.form.get('api_key', config.INDODAX_API_KEY),
            'secret_key': new_secret,
            'dca_interval_hours': int(request.form.get('dca_interval_hours', 24)),
            'dca_amount_idr': int(request.form.get('dca_amount_idr', 100000)),
            'trading_pair': request.form.get('trading_pair', 'btcidr'),
            'dry_run': request.form.get('dry_run') == 'on'
        }
        
        save_config(config_data)
        return redirect(url_for('settings', success=1))
    
    current_config = load_config()
    success = request.args.get('success', 0)
    
    return render_template('settings.html', 
        config=current_config, 
        success=success,
        pairs=[
            {'value': 'btcidr', 'label': 'BTC/IDR'},
            {'value': 'ethidr', 'label': 'ETH/IDR'},
            {'value': 'bchidr', 'label': 'BCH/IDR'},
            {'value': 'dogeidr', 'label': 'DOGE/IDR'},
            {'value': 'xrpidr', 'label': 'XRP/IDR'}
        ]
    )

@app.route('/api/start', methods=['POST'])
def api_start():
    """Start bot API"""
    if not bot_state['running']:
        bot_state['running'] = True
        return jsonify({'success': True, 'message': 'Bot started'})
    return jsonify({'success': False, 'message': 'Bot already running'})

@app.route('/api/stop', methods=['POST'])
def api_stop():
    """Stop bot API"""
    if bot_state['running']:
        bot_state['running'] = False
        return jsonify({'success': True, 'message': 'Bot stopped'})
    return jsonify({'success': False, 'message': 'Bot not running'})

@app.route('/api/trade', methods=['POST'])
def api_manual_trade():
    """Execute manual trade"""
    try:
        client = IndodaxClient(config.INDODAX_API_KEY, config.INDODAX_SECRET_KEY)
        
        if config.DRY_RUN:
            # Dry run mode
            ticker = client.get_ticker(config.TRADING_PAIR)
            if 'error' in ticker:
                return jsonify({'success': False, 'message': f"Gagal ambil harga: {ticker['error']}"})
            
            current_price = float(ticker.get('last', 0))
            if current_price <= 0:
                return jsonify({'success': False, 'message': 'Harga tidak valid dari exchange'})
            
            crypto_amount = config.DCA_AMOUNT_IDR / current_price
            
            # Update bot data
            bot_data = load_bot_data()
            bot_data['last_trade'] = datetime.now().isoformat()
            bot_data['total_trades'] = int(bot_data.get('total_trades', 0) or 0) + 1
            bot_data['total_invested'] = float(bot_data.get('total_invested', 0) or 0) + config.DCA_AMOUNT_IDR
            bot_data['total_crypto_bought'] = float(bot_data.get('total_crypto_bought', 0) or 0) + crypto_amount
            trades_list = list(bot_data.get('trades', []) or [])
            trades_list.append({
                'timestamp': datetime.now().isoformat(),
                'price': current_price,
                'amount_idr': config.DCA_AMOUNT_IDR,
                'crypto_amount': crypto_amount,
                'dry_run': True
            })
            bot_data['trades'] = trades_list
            save_bot_data(bot_data)
            
            return jsonify({
                'success': True, 
                'message': f'[DRY RUN] Would buy {crypto_amount:.8f} at Rp {current_price:,.0f}'
            })
        else:
            # Real trade — ambil harga dulu untuk logging
            ticker = client.get_ticker(config.TRADING_PAIR)
            if 'error' in ticker:
                return jsonify({'success': False, 'message': f"Gagal ambil harga ticker: {ticker['error']}"})
            
            current_price = float(ticker.get('last', 0))
            if current_price <= 0:
                return jsonify({'success': False, 'message': 'Harga tidak valid dari exchange'})

            result = client.buy_market(config.TRADING_PAIR, config.DCA_AMOUNT_IDR)
            
            if isinstance(result, dict) and 'error' in result:
                return jsonify({'success': False, 'message': f"Order gagal: {result['error']}"})
            
            # Update bot data
            bot_data = load_bot_data()
            crypto_amount = float(result.get('credit', 0) or 0) if isinstance(result, dict) else 0.0
            if not crypto_amount:
                crypto_amount = config.DCA_AMOUNT_IDR / current_price
            
            trade_price = float(result.get('price', current_price) or current_price) if isinstance(result, dict) else current_price
            order_id = result.get('order_id', 'N/A') if isinstance(result, dict) else 'N/A'
            
            bot_data['last_trade'] = datetime.now().isoformat()
            bot_data['total_trades'] = int(bot_data.get('total_trades', 0) or 0) + 1
            bot_data['total_invested'] = float(bot_data.get('total_invested', 0) or 0) + config.DCA_AMOUNT_IDR
            bot_data['total_crypto_bought'] = float(bot_data.get('total_crypto_bought', 0) or 0) + crypto_amount
            trades_list = list(bot_data.get('trades', []) or [])
            trades_list.append({
                'timestamp': datetime.now().isoformat(),
                'price': trade_price,
                'amount_idr': config.DCA_AMOUNT_IDR,
                'crypto_amount': crypto_amount,
                'order_id': str(order_id),
                'dry_run': False
            })
            bot_data['trades'] = trades_list
            save_bot_data(bot_data)
            
            return jsonify({
                'success': True, 
                'message': f'Trade berhasil! Order ID: {order_id} | {crypto_amount:.8f} BTC @ Rp {trade_price:,.0f}'
            })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Server error: {str(e)}'})


@app.route('/trades')
def trades():
    """Trade history page"""
    bot_data = load_bot_data()
    trades = list(bot_data.get('trades', []) or [])
    
    # Return last 100 trades
    recent_trades = trades[-100:][::-1]
    
    return render_template('trades.html', trades=recent_trades)

@app.route('/logs')
def logs():
    """View logs page"""
    try:
        with open(config.LOG_FILE, 'r') as f:
            log_content = f.read()
    except FileNotFoundError:
        log_content = 'No logs available yet.'
    
    return render_template('logs.html', logs=log_content)

if __name__ == '__main__':
    print("=" * 50)
    print("🌐 Exbot DCA Bot Dashboard")
    print("=" * 50)
    print("📍 Dashboard akan berjalan di: http://localhost:5000")
    print("⏹️  Tekan Ctrl+C untuk menghentikan server")
    print("=" * 50)
    
    app.run(host='0.0.0.0', port=5000, debug=True)