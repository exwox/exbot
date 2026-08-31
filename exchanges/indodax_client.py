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
from urllib.parse import urlencode

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

    def __init__(self, api_key: str = "", secret_key: str = "",
                 api_version: str = "v1"):
        super().__init__(api_key, secret_key)
        self.api_version = str(api_version or 'v1').strip().lower()
        if self.api_version not in ('v1', 'v2'):
            raise ValueError('Versi API Indodax harus v1 atau v2')
        # Private API endpoints
        self.base_url = 'https://indodax.com/tapi'
        self.v2_base_url = 'https://api.indodax.com'
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
        p = str(pair).strip().lower().replace('/', '').replace('-', '').replace(' ', '')
        if '_' not in p:
            if p.endswith('idr'):
                p = p[:-3] + '_' + p[-3:]
            elif p.endswith('usdt'):
                p = p[:-4] + '_' + p[-4:]
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
            hashlib.sha256
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
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
        }

    @staticmethod
    def _normalize_symbol_v2(pair: str) -> str:
        return str(pair or 'btcidr').strip().upper().replace(
            '_', '').replace('/', '').replace('-', '').replace(' ', '')

    @staticmethod
    def _decimal_string(value: Any, places: int = 8) -> str:
        decimal_value = Decimal(str(value or 0)).quantize(
            Decimal('1').scaleb(-places), rounding=ROUND_DOWN)
        return format(decimal_value, 'f').rstrip('0').rstrip('.') or '0'

    @staticmethod
    def _v2_error(result: Any, status_code: int = 0) -> Optional[dict]:
        if isinstance(result, dict) and result.get('code') is not None:
            code = result.get('code')
            try:
                is_error = int(code) < 0
            except (TypeError, ValueError):
                is_error = bool(result.get('msg') or result.get('message'))
            if is_error:
                return {
                    'error': str(result.get('msg') or result.get('message') or
                                 f'Indodax API error {code}'),
                    'code': code,
                    'status_code': status_code,
                }
        if status_code >= 400:
            message = result.get('msg') if isinstance(result, dict) else result
            return {
                'error': str(message or f'Indodax HTTP {status_code}'),
                'status_code': status_code,
            }
        return None

    def _private_v2(self, http_method: str, path: str,
                    params: Optional[dict] = None) -> Any:
        """Call one signed Trade API v2 endpoint without cross-version fallback."""
        payload = dict(params or {})
        payload['timestamp'] = str(int(time.time() * 1000))
        payload['recvWindow'] = '5000'
        query_string = urlencode(payload)
        headers = self._get_headers_v2(query_string)
        url = f'{self.v2_base_url}{path}'
        method = str(http_method).upper()
        kwargs = {'headers': headers}
        if method == 'POST':
            kwargs['data'] = query_string
        else:
            url = f'{url}?{query_string}'

        response = self._request_with_retry(method, url, **kwargs)
        if isinstance(response, dict) and response.get('error'):
            return response
        try:
            result = response.json()
        except Exception as error:
            return {'error': f'Failed to parse JSON response: {error}'}
        api_error = self._v2_error(result, int(getattr(response, 'status_code', 0)))
        return api_error or result

    @staticmethod
    def _as_float(*values: Any) -> float:
        for value in values:
            if value in (None, ''):
                continue
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
        return 0.0

    def _normalize_v2_order(self, pair: str, order: dict) -> dict:
        raw = dict(order or {})
        requested = self._as_float(
            raw.get('origQty'), raw.get('oriQty'), raw.get('quantity'))
        executed = self._as_float(
            raw.get('executedQty'), raw.get('filledQty'),
            raw.get('filled_amount'))
        price = self._as_float(raw.get('price'))
        requested_quote = self._as_float(
            raw.get('origQuoteOrderQty'), raw.get('quoteOrderQty'))
        filled_quote = self._as_float(
            raw.get('cummulativeQuoteQty'), raw.get('cumulativeQuoteQty'),
            raw.get('quoteQty'), raw.get('filled_quote'))
        if filled_quote <= 0 and executed > 0 and price > 0:
            filled_quote = executed * price

        status_map = {
            'NEW': 'open',
            'OPEN': 'open',
            'PENDING': 'open',
            'PARTIALLY_FILLED': 'partially_filled',
            'PARTIAL': 'partially_filled',
            'FILLED': 'filled',
            'DONE': 'filled',
            'CANCELLED': 'cancelled',
            'CANCELED': 'cancelled',
            'REJECTED': 'rejected',
            'EXPIRED': 'expired',
        }
        remote_status = str(raw.get('status') or '').upper()
        status = status_map.get(remote_status, remote_status.lower() or 'open')
        if not remote_status:
            if requested > 0 and executed >= requested:
                status = 'filled'
            elif executed > 0:
                status = 'partially_filled'

        side = str(raw.get('side') or '').lower()
        order_kind = str(raw.get('type') or '').lower()
        normalized = dict(raw)
        normalized.update({
            'order_id': str(raw.get('orderId') or raw.get('order_id') or ''),
            'client_order_id': str(
                raw.get('clientOrderId') or raw.get('client_order_id') or ''),
            'symbol': str(raw.get('symbol') or self._normalize_symbol_v2(pair)),
            'side': side,
            # Existing UI/worker code uses `type` as buy/sell.
            'type': side,
            'order_type': order_kind,
            'price': price,
            'amount': requested,
            'amount_remaining': max(requested - executed, 0.0),
            'amount_idr': requested_quote,
            'amount_remaining_idr': max(requested_quote - filled_quote, 0.0),
            'filled_amount': executed,
            'filled_quote': filled_quote,
            'status': status,
            'api_version': 'v2',
        })
        return normalized

    def _v2_trade_rows(self, pair: str, limit: int = 1000,
                       order_id: str = '') -> Any:
        params: dict[str, Any] = {
            'symbol': self._normalize_symbol_v2(pair),
            'limit': min(max(int(limit), 1), 1000),
        }
        if order_id:
            params['orderId'] = str(order_id)
        result = self._private_v2('GET', '/api/v2/myTrades', params)
        if isinstance(result, dict) and result.get('error'):
            return result
        rows = result.get('data', []) if isinstance(result, dict) else result
        return rows if isinstance(rows, list) else []

    def _enrich_v2_order(self, pair: str, order: dict) -> dict:
        normalized = self._normalize_v2_order(pair, order)
        if normalized.get('filled_amount', 0) <= 0:
            return normalized
        order_id = str(normalized.get('order_id') or '')
        client_id = str(normalized.get('client_order_id') or '')
        trades = self._v2_trade_rows(pair, order_id=order_id)
        if isinstance(trades, dict) and trades.get('error'):
            return normalized
        matching = [trade for trade in trades if isinstance(trade, dict) and (
            (order_id and str(trade.get('orderId') or '') == order_id) or
            (not order_id and client_id and
             str(trade.get('clientOrderId') or '') == client_id))]
        if not matching:
            return normalized

        filled_amount = sum(self._as_float(row.get('qty')) for row in matching)
        filled_quote = sum(self._as_float(
            row.get('quoteQty'),
            self._as_float(row.get('qty')) * self._as_float(row.get('price')))
                           for row in matching)
        fee_by_asset: dict[str, float] = {}
        for row in matching:
            asset = str(row.get('commissionAsset') or '').lower()
            if asset:
                fee_by_asset[asset] = fee_by_asset.get(asset, 0.0) + \
                    self._as_float(row.get('commission'))
        if filled_amount > 0:
            normalized['filled_amount'] = filled_amount
            normalized['amount_remaining'] = max(
                self._as_float(normalized.get('amount')) - filled_amount, 0.0)
        if filled_quote > 0:
            normalized['filled_quote'] = filled_quote
            normalized['average_price'] = (
                filled_quote / filled_amount if filled_amount > 0 else 0.0)
        normalized['fee_breakdown'] = fee_by_asset
        normalized['fee_known'] = True
        if len(fee_by_asset) == 1:
            normalized['fee_asset'], normalized['fee_amount'] = next(
                iter(fee_by_asset.items()))
        elif not fee_by_asset:
            normalized['fee_asset'] = ''
            normalized['fee_amount'] = 0.0
        return normalized

    def test_connection(self) -> dict:
        return self.get_balance()

    def get_balance(self) -> dict:
        if self.api_version == 'v2':
            result = self._private_v2('GET', '/api/v2/account')
            if isinstance(result, dict) and result.get('error'):
                return result
            balances = result.get('balances', []) if isinstance(result, dict) else []
            available: dict[str, float] = {}
            held: dict[str, float] = {}
            for item in balances if isinstance(balances, list) else []:
                if not isinstance(item, dict):
                    continue
                asset = str(item.get('asset') or '').lower()
                if not asset:
                    continue
                available[asset] = self._as_float(item.get('free'))
                held[asset] = self._as_float(item.get('locked'))
            return {
                'balance': available,
                'balance_hold': held,
                'can_trade': bool(result.get('canTrade', True)),
                'can_withdraw': bool(result.get('canWithdraw', False)),
                'can_deposit': bool(result.get('canDeposit', False)),
                'api_version': 'v2',
                'raw': result,
            }
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
        if self.api_version == 'v2':
            params = {
                'symbol': self._normalize_symbol_v2(pair),
                'side': 'BUY',
            }
            if Decimal(str(price or 0)) <= 0:
                quote_asset = self._normalize_pair(pair).split('_', 1)[-1]
                if quote_asset != 'idr':
                    return {
                        'error': ('MARKET BUY Trade API v2 hanya diaktifkan '
                                  'untuk pasangan quote IDR')
                    }
                params.update({
                    'type': 'MARKET',
                    'quoteOrderQty': str(int(Decimal(str(amount or 0)))),
                })
            else:
                params.update({
                    'type': 'LIMIT',
                    'timeInForce': 'GTC',
                    'price': self._decimal_string(price),
                    'quantity': self._decimal_string(amount),
                })
            if client_order_id:
                params['newClientOrderId'] = str(client_order_id)[:36]
            result = self._private_v2('POST', '/api/v2/order', params)
            if isinstance(result, dict) and not result.get('error'):
                return self._normalize_v2_order(pair, result)
            return result

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
        if self.api_version == 'v2':
            params = {
                'symbol': self._normalize_symbol_v2(pair),
                'side': 'SELL',
                'type': 'LIMIT',
                'timeInForce': 'GTC',
                'price': self._decimal_string(price),
                'quantity': self._decimal_string(amount),
            }
            if client_order_id:
                params['newClientOrderId'] = str(client_order_id)[:36]
            result = self._private_v2('POST', '/api/v2/order', params)
            if isinstance(result, dict) and not result.get('error'):
                return self._normalize_v2_order(pair, result)
            return result

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
        if self.api_version == 'v2':
            params = {
                'symbol': self._normalize_symbol_v2(pair),
                'side': 'SELL',
                'type': 'MARKET',
                'quantity': self._decimal_string(crypto_amount),
            }
            if client_order_id:
                params['newClientOrderId'] = str(client_order_id)[:36]
            result = self._private_v2('POST', '/api/v2/order', params)
            if isinstance(result, dict) and not result.get('error'):
                return self._normalize_v2_order(pair, result)
            return result

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
        if self.api_version == 'v2':
            result = self._private_v2('GET', '/api/v2/order', {
                'symbol': self._normalize_symbol_v2(pair),
                'orderId': str(order_id),
            })
            if isinstance(result, dict) and not result.get('error'):
                return self._enrich_v2_order(pair, result)
            return result

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
        if self.api_version == 'v2':
            result = self._private_v2('GET', '/api/v2/order', {
                'symbol': self._normalize_symbol_v2(pair),
                'origClientOrderId': str(client_order_id),
            })
            if isinstance(result, dict) and not result.get('error'):
                return self._enrich_v2_order(pair, result)
            return result

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
        if self.api_version == 'v2':
            result = self._private_v2('GET', '/api/v2/openOrders', {
                'symbol': self._normalize_symbol_v2(pair),
            })
            if isinstance(result, dict) and result.get('error'):
                return result
            rows = result.get('data', []) if isinstance(result, dict) else result
            if not isinstance(rows, list):
                return []
            return [self._normalize_v2_order(pair, row)
                    for row in rows if isinstance(row, dict)]

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
        if self.api_version == 'v2':
            result = self._private_v2('DELETE', '/api/v2/order', {
                'symbol': self._normalize_symbol_v2(pair),
                'orderId': str(order_id),
            })
            if isinstance(result, dict) and not result.get('error'):
                return self._normalize_v2_order(pair, result)
            return result
        return self._post_private('cancelOrder', {
            'pair': self._normalize_pair(pair),
            'order_id': str(order_id),
            'type': str(order_type)
        })

    def cancel_order_by_client_id(self, client_order_id='', pair='') -> dict:
        if self.api_version == 'v2':
            if not pair:
                return {
                    'error': ('Pair/symbol wajib diisi untuk membatalkan '
                              'order v2 berdasarkan client order ID')
                }
            result = self._private_v2('DELETE', '/api/v2/order', {
                'symbol': self._normalize_symbol_v2(pair),
                'origClientOrderId': str(client_order_id),
            })
            if isinstance(result, dict) and not result.get('error'):
                return self._normalize_v2_order(pair, result)
            return result
        return self._post_private('cancelByClientOrderId', {
            'client_order_id': str(client_order_id),
        })

    def get_trade_history(self, pair='btcidr', limit=10) -> list:
        if self.api_version == 'v2':
            rows = self._v2_trade_rows(pair, limit=limit)
            if isinstance(rows, dict) and rows.get('error'):
                return rows
            normalized = []
            for trade in rows:
                if not isinstance(trade, dict):
                    continue
                amount = self._as_float(trade.get('qty'))
                price = self._as_float(trade.get('price'))
                quote = self._as_float(trade.get('quoteQty'), amount * price)
                is_buyer = bool(trade.get('isBuyer'))
                normalized.append({
                    **trade,
                    'trade_id': str(trade.get('tradeId') or ''),
                    'order_id': str(trade.get('orderId') or ''),
                    'client_order_id': str(trade.get('clientOrderId') or ''),
                    'type': 'buy' if is_buyer else 'sell',
                    'side': 'buy' if is_buyer else 'sell',
                    'price': price,
                    'amount': amount,
                    'amount_idr': quote,
                    'filled_quote': quote,
                    'fee_amount': self._as_float(trade.get('commission')),
                    'fee_asset': str(
                        trade.get('commissionAsset') or '').lower(),
                    'fee_known': True,
                    'timestamp': trade.get('time'),
                    'api_version': 'v2',
                })
            return normalized

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
