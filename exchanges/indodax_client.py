# Refactored Indodax API Client
# - No global API key/secret dependency
# - Credentials via constructor
# - Added timeout, retry, rate-limit handling
# - Inherits from BaseExchangeClient

import time
import re
import hashlib
import hmac
import threading
from decimal import Decimal, ROUND_DOWN
from typing import Any, Optional

import requests

from exchanges.base_client import BaseExchangeClient
from config.settings import MAX_API_RETRIES, API_RETRY_DELAY, API_TIMEOUT, RATE_LIMIT_CALLS_PER_SECOND


class IndodaxClient(BaseExchangeClient):
    """
    Indodax API Client - setiap instance hanya menangani SATU akun.
    Tidak lagi membaca INDODAX_API_KEY / INDODAX_SECRET_KEY dari config global.
    """

    _account_lock = threading.Lock()
    _account_state: dict[str, dict] = {}

    def __init__(self, api_key: str = "", secret_key: str = ""):
        super().__init__(api_key, secret_key)
        # Private API endpoints
        self.base_url = 'https://indodax.com/tapi'
        self.trade_api_url = 'https://tapi.indodax.com'
        self.trade_api_alt_url = 'https://tapi.btcapi.net'
        # Public API endpoints
        self.public_url = 'https://indodax.com/api'
        self.chart_url = 'https://indodax.com/tradingview'
        self.public_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        # Rate limiting
        self._rate_limit_delay = 1.0 / RATE_LIMIT_CALLS_PER_SECOND

    def _account_key(self) -> str:
        return self.api_key or "anonymous"

    def _next_nonce(self) -> int:
        with self._account_lock:
            state = self._account_state.setdefault(self._account_key(), {})
            candidate = int(time.time() * 1000)
            nonce = max(candidate, int(state.get('nonce', 0)) + 1)
            state['nonce'] = nonce
            return nonce

    def _update_nonce_min(self, required_min: int):
        with self._account_lock:
            state = self._account_state.setdefault(self._account_key(), {})
            state['nonce'] = max(int(state.get('nonce', 0)), int(required_min) + 10)

    def _post_private(self, method: str, params: Optional[dict] = None) -> Any:
        if params is None:
            params = {}

        for attempt in range(4):
            post_data = {
                'method': method,
                'nonce': self._next_nonce(),
            }
            post_data.update(params)
            headers = self._get_headers(post_data)

            response = self._request_with_retry('POST', self.base_url, data=post_data, headers=headers)
            if isinstance(response, dict) and 'error' in response:
                err_msg = str(response['error'])
                match = re.search(r'Nonce must be greater than (\d+)', err_msg, re.IGNORECASE)
                if match:
                    required_min = int(match.group(1))
                    self._update_nonce_min(required_min)
                    time.sleep(0.1)
                    continue
                return response

            try:
                result = response.json()
            except Exception as e:
                return {'error': f"Failed to parse JSON response: {e}"}

            if isinstance(result, dict):
                if result.get('success') == 1:
                    return result.get('return', {})
                else:
                    err_msg = str(result.get('error', 'Unknown error'))
                    match = re.search(r'Nonce must be greater than (\d+)', err_msg, re.IGNORECASE)
                    if match:
                        required_min = int(match.group(1))
                        self._update_nonce_min(required_min)
                        time.sleep(0.1)
                        continue
                    return {'error': err_msg}

        return {'error': 'Max retries exceeded for private API request'}

    def _rate_limit(self):
        with self._account_lock:
            state = self._account_state.setdefault(self._account_key(), {})
            now = time.time()
            elapsed = now - float(state.get('last_request_time', 0.0))
            if elapsed < self._rate_limit_delay:
                time.sleep(self._rate_limit_delay - elapsed)
            state['last_request_time'] = time.time()

    def _request_with_retry(self, method: str, url: str, **kwargs) -> Any:
        last_error = None
        for attempt in range(MAX_API_RETRIES):
            try:
                self._rate_limit()
                response = requests.request(method, url, timeout=API_TIMEOUT, **kwargs)
                return response
            except requests.exceptions.Timeout as e:
                last_error = f"Timeout: {e}"
                if attempt < MAX_API_RETRIES - 1:
                    time.sleep(API_RETRY_DELAY * (attempt + 1))
            except requests.exceptions.ConnectionError as e:
                last_error = f"ConnectionError: {e}"
                if attempt < MAX_API_RETRIES - 1:
                    time.sleep(API_RETRY_DELAY * (attempt + 1))
            except Exception as e:
                last_error = str(e)
                if attempt < MAX_API_RETRIES - 1:
                    time.sleep(API_RETRY_DELAY)
        return {'error': last_error}

    def _normalize_pair(self, pair):
        if not pair:
            return 'btc_idr'
        p = pair.lower()
        if '_' not in p:
            if p.endswith('idr'):
                p = p[:-3] + '_' + p[-3:]
        return p

    def _generate_signature(self, post_data):
        post_query = '&'.join([f"{key}={value}" for key, value in post_data.items()])
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            post_query.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        return signature

    def _generate_signature_v2(self, query_string):
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        return signature

    def _get_headers(self, post_data):
        signature = self._generate_signature(post_data)
        return {
            'Key': self.api_key,
            'Sign': signature,
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        }

    def _get_headers_v2(self, query_string):
        signature = self._generate_signature_v2(query_string)
        return {
            'X-APIKEY': self.api_key,
            'Sign': signature,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        }

    def test_connection(self) -> dict:
        return self.get_balance()

    def get_balance(self) -> dict:
        return self._post_private('getInfo')

    def get_ticker(self, pair='btcidr') -> dict:
        try:
            response = requests.get(
                f'{self.public_url}/ticker_all',
                headers=self.public_headers,
                timeout=API_TIMEOUT
            )
            result = response.json()
            if isinstance(result, dict) and 'tickers' in result:
                pair_formatted = pair.lower()
                if '_' not in pair_formatted and pair_formatted.endswith('idr'):
                    pair_formatted = pair_formatted[:-3] + '_idr'
                tickers = result['tickers']
                if pair_formatted in tickers:
                    ticker_data = tickers[pair_formatted]
                    return {
                        'last': ticker_data.get('last', 0),
                        'high': ticker_data.get('high', 0),
                        'low': ticker_data.get('low', 0),
                        'buy': ticker_data.get('buy', 0),
                        'sell': ticker_data.get('sell', 0)
                    }
                else:
                    return {'error': f'Pair {pair_formatted} not found in tickers'}
            else:
                return {'error': 'Invalid ticker_all response'}
        except Exception as e:
            return {'error': str(e)}

    def get_ticker_all(self) -> dict:
        try:
            response = requests.get(
                f'{self.public_url}/ticker_all',
                headers=self.public_headers,
                timeout=API_TIMEOUT
            )
            return response.json()
        except Exception as e:
            return {'error': str(e)}

    def get_orderbook(self, pair='btcidr') -> dict:
        try:
            pair_lower = pair.lower()
            response = requests.get(
                f'{self.public_url}/{pair_lower}/depth',
                headers=self.public_headers,
                timeout=API_TIMEOUT
            )
            result = response.json()
            if isinstance(result, dict) and ('buy' in result or 'sell' in result):
                return result
            else:
                return {'error': 'Invalid orderbook data'}
        except Exception as e:
            return {'error': str(e)}

    def buy(self, pair='btcidr', price=0, amount=0,
            client_order_id='') -> dict:
        """Melakukan order beli limit atau market"""
        coin = pair.lower().replace('_', '').replace('idr', '')
        if float(price) == 0:
            target_idr = max(float(amount), 10100.0)
            params = {
                'pair': self._normalize_pair(pair),
                'type': 'buy',
                'order_type': 'market',
                'idr': str(int(target_idr))
            }
        else:
            price_val = float(price)
            amount_val = float(amount)
            if price_val * amount_val < 10000.0:
                amount_val = round(10100.0 / price_val, 8)
                if price_val * amount_val < 10000.0:
                    amount_val += 0.00000001

            amount_str = f"{amount_val:.8f}".rstrip('0').rstrip('.')
            if '.' in amount_str:
                parts = amount_str.split('.')
                if len(parts[1]) > 8:
                    amount_str = parts[0] + '.' + parts[1][:8]

            params = {
                'pair': self._normalize_pair(pair),
                'type': 'buy',
                'order_type': 'limit',
                'price': str(int(price_val)),
                coin: amount_str
            }
        if client_order_id:
            params['client_order_id'] = str(client_order_id)[:36]
        return self._post_private('trade', params)

    def buy_market(self, pair='btcidr', amount_idr=0,
                   client_order_id='') -> dict:
        return self.buy(pair, 0, amount_idr, client_order_id)

    # ============================================================
    # Sell Order
    # ============================================================
    def sell(self, pair='btcidr', price=0, amount=0,
             client_order_id='') -> dict:
        coin = pair.lower().replace('_', '').replace('idr', '')
        amount_decimal = Decimal(str(amount)).quantize(
            Decimal('0.00000001'), rounding=ROUND_DOWN)
        amount_str = format(amount_decimal, 'f').rstrip('0').rstrip('.')
        params = {
            'pair': self._normalize_pair(pair),
            'type': 'sell',
            'price': str(price),
            'amount': amount_str,
            coin: amount_str
        }
        if client_order_id:
            params['client_order_id'] = str(client_order_id)[:36]
        return self._post_private('trade', params)

    def sell_market(self, pair='btcidr', crypto_amount=0,
                    client_order_id='') -> dict:
        coin = pair.lower().replace('_', '').replace('idr', '')
        amount_decimal = Decimal(str(crypto_amount)).quantize(
            Decimal('0.00000001'), rounding=ROUND_DOWN)
        amount_str = format(amount_decimal, 'f').rstrip('0').rstrip('.')
        params = {
            'pair': self._normalize_pair(pair),
            'type': 'sell',
            'order_type': 'market',
            coin: amount_str,
        }
        if client_order_id:
            params['client_order_id'] = str(client_order_id)[:36]
        return self._post_private('trade', params)

    # ============================================================
    # Order Status & Management
    # ============================================================
    def _normalize_order_payload(self, pair: str, order: dict) -> dict:
        """Expose consistent requested/remaining/filled quantities.

        Indodax field names depend on the base asset (for example
        ``order_btc``/``remain_btc``). Keeping the raw fields while adding
        normalized values makes partial-fill handling deterministic.
        """
        normalized = dict(order or {})
        coin = self._normalize_pair(pair).split('_', 1)[0]

        def as_float(*values):
            for value in values:
                if value is None or value == '':
                    continue
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
            return 0.0

        requested = as_float(
            normalized.get('amount'), normalized.get(f'order_{coin}'),
            normalized.get('order_amount'))
        remaining_keys = ('amount_remaining', f'remain_{coin}', 'remain_amount')
        remaining_present = any(
            key in normalized and normalized.get(key) not in (None, '')
            for key in remaining_keys
        )
        remaining = as_float(*(normalized.get(key) for key in remaining_keys))
        price = as_float(normalized.get('price'))
        requested_quote = as_float(
            normalized.get('amount_idr'), normalized.get('order_idr'),
            normalized.get('order_rp'))
        remaining_quote_keys = (
            'amount_remaining_idr', 'remain_idr', 'remain_rp')
        remaining_quote_present = any(
            key in normalized and normalized.get(key) not in (None, '')
            for key in remaining_quote_keys
        )
        remaining_quote = as_float(
            *(normalized.get(key) for key in remaining_quote_keys))
        # Buy orders are represented by Indodax as order_idr/remain_idr,
        # whereas sell orders use order_<coin>/remain_<coin>. Convert the
        # quote representation into a base quantity for one common contract.
        if requested <= 0 and requested_quote > 0 and price > 0:
            requested = requested_quote / price
            remaining = remaining_quote / price
            remaining_present = remaining_quote_present
        explicit_filled = as_float(
            normalized.get('filled_amount'), normalized.get(f'filled_{coin}'),
            normalized.get('executed_amount'))
        filled = (max(requested - remaining, 0.0)
                  if requested > 0 and remaining_present else explicit_filled)
        filled_quote = as_float(normalized.get('filled_quote'))
        if filled_quote <= 0:
            filled_quote = (max(requested_quote - remaining_quote, 0.0)
                            if requested_quote > 0 and remaining_present
                            else filled * price)

        explicit = str(normalized.get('status', '')).lower()
        if explicit in ('cancelled', 'canceled', 'rejected', 'expired'):
            status = explicit
        elif explicit in ('filled', 'done', 'closed'):
            status = 'filled'
            if filled <= 0:
                filled = requested
                filled_quote = requested_quote or filled * price
        elif requested > 0 and remaining_present and remaining <= 0:
            status = 'filled'
        elif filled > 0:
            status = 'partially_filled'
        else:
            status = explicit or 'open'

        normalized.update({
            'amount': requested,
            'amount_remaining': remaining,
            'amount_idr': requested_quote,
            'amount_remaining_idr': remaining_quote,
            'filled_amount': filled,
            'filled_quote': filled_quote,
            'status': status,
        })
        return normalized

    def get_order_status(self, pair='btcidr', order_id='') -> dict:
        ret = self._post_private('getOrder', {
            'pair': self._normalize_pair(pair),
            'order_id': str(order_id)
        })
        if isinstance(ret, dict) and 'order' in ret:
            order = ret['order']
            if isinstance(order, dict):
                order.setdefault('order_id', str(order_id))
                return self._normalize_order_payload(pair, order)
            return order
        if isinstance(ret, dict) and 'error' not in ret:
            ret.setdefault('order_id', str(order_id))
            return self._normalize_order_payload(pair, ret)
        return ret

    def get_order_by_client_id(self, pair='btcidr', client_order_id='') -> dict:
        ret = self._post_private('getOrderByClientOrderId', {
            'client_order_id': str(client_order_id),
        })
        if isinstance(ret, dict) and 'order' in ret:
            order = ret['order']
            if isinstance(order, dict):
                return self._normalize_order_payload(pair, order)
            return order
        if isinstance(ret, dict) and 'error' not in ret:
            return self._normalize_order_payload(pair, ret)
        return ret

    def get_open_orders(self, pair='btcidr') -> list:
        return_value = self._post_private('openOrders', {'pair': self._normalize_pair(pair)})
        if isinstance(return_value, dict) and 'error' in return_value:
            return return_value

        if isinstance(return_value, dict):
            orders_dict: Any = return_value.get('orders', {})
            orders_list: list = []
            if isinstance(orders_dict, dict):
                for oid in orders_dict:
                    o = orders_dict[oid]
                    o['order_id'] = str(oid)
                    orders_list.append(o)
            elif isinstance(orders_dict, list):
                orders_list = orders_dict

            return [self._normalize_order_payload(pair, order)
                    for order in orders_list]
        elif isinstance(return_value, list):
            return [self._normalize_order_payload(pair, order)
                    for order in return_value if isinstance(order, dict)]
        return []

    def cancel_order(self, pair='btcidr', order_id='', order_type='') -> dict:
        return self._post_private('cancelOrder', {
            'pair': self._normalize_pair(pair),
            'order_id': str(order_id),
            'type': str(order_type)
        })

    def cancel_order_by_client_id(self, client_order_id='') -> dict:
        return self._post_private('cancelByClientOrderId', {
            'client_order_id': str(client_order_id),
        })

    def get_trade_history(self, pair='btcidr', limit=10) -> list:
        return_value = self._post_private('tradeHistory', {
            'pair': self._normalize_pair(pair)
        })
        if isinstance(return_value, dict) and 'error' in return_value:
            return return_value
        if isinstance(return_value, dict):
            trades = return_value.get('trades', [])
            if isinstance(trades, list):
                return trades
            return []
        elif isinstance(return_value, list):
            return return_value
        return []

    def get_ohlc(self, pair='btcidr', timeframe='1h', limit=100) -> list:
        tf_map = {
            '1m': '1', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360',
            '12h': '720', '1d': '1D', '3d': '3D', '1w': '1W'
        }
        tf = tf_map.get(timeframe, '60')
        try:
            now = int(time.time())
            duration_map = {
                '1': 60, '5': 300, '15': 900, '30': 1800,
                '60': 3600, '120': 7200, '240': 14400, '360': 21600,
                '720': 43200, '1D': 86400, '3D': 259200, '1W': 604800
            }
            duration = duration_map.get(tf, 3600) * limit
            from_time = now - duration

            response = requests.get(
                f'{self.chart_url}/history_v2',
                params={
                    'symbol': pair.upper(),
                    'tf': tf,
                    'from': from_time,
                    'to': now
                },
                timeout=API_TIMEOUT
            )
            result = response.json()
            if isinstance(result, list):
                candles = []
                for candle in result:
                    candles.append({
                        'timestamp': candle.get('Time', 0),
                        'open': float(candle.get('Open', 0)),
                        'high': float(candle.get('High', 0)),
                        'low': float(candle.get('Low', 0)),
                        'close': float(candle.get('Close', 0)),
                        'volume': float(candle.get('Volume', 0))
                    })
                return candles
            elif isinstance(result, dict) and 'candles' in result:
                return result['candles']
            else:
                return []
        except Exception as e:
            return {'error': str(e)}

    def get_pairs(self) -> dict:
        try:
            response = requests.get(
                f'{self.public_url}/pairs',
                headers=self.public_headers,
                timeout=API_TIMEOUT
            )
            return response.json()
        except Exception as e:
            return {'error': str(e)}
