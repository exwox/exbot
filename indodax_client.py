"""
Indodax API Client
Client untuk berinteraksi dengan API Indodax
Updated sesuai dokumentasi resmi: https://github.com/btcid/indodax-official-api-docs
"""

import time
import re
import hashlib
import hmac
from typing import Any

import requests
from config import INDODAX_API_KEY, INDODAX_SECRET_KEY


import threading


class IndodaxClient:
    _nonce_counter = int(time.time() * 1000)
    _nonce_lock = threading.Lock()

    @classmethod
    def _next_nonce(cls) -> int:
        with cls._nonce_lock:
            now = int(time.time() * 1000)
            if now > cls._nonce_counter:
                cls._nonce_counter = now
            else:
                cls._nonce_counter += 1
            return cls._nonce_counter

    def __init__(self, api_key, secret_key):
        self.api_key = api_key
        self.secret_key = secret_key
        # Private API endpoints
        self.base_url = 'https://indodax.com/tapi'  # Legacy endpoint
        self.trade_api_url = 'https://tapi.indodax.com'  # Trade API 2.0
        self.trade_api_alt_url = 'https://tapi.btcapi.net'  # Alternative endpoint
        # Public API endpoints
        self.public_url = 'https://indodax.com/api'
        self.chart_url = 'https://indodax.com/tradingview'
        self.public_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    
    def _normalize_pair(self, pair: str) -> str:
        if not pair:
            return 'btc_idr'
        p = str(pair).strip().lower().replace('/', '').replace('-', '').replace(' ', '')
        if '_' not in p:
            if p.endswith('idr') and len(p) > 3:
                p = p[:-3] + '_idr'
            elif p.endswith('usdt') and len(p) > 4:
                p = p[:-4] + '_usdt'
        return p
    
    def _generate_signature(self, post_data):
        """Generate HMAC-SHA512 signature untuk legacy endpoint"""
        post_query = '&'.join([f"{key}={value}" for key, value in post_data.items()])
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            post_query.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        return signature
    
    def _generate_signature_v2(self, query_string):
        """Generate HMAC-SHA512 signature untuk Trade API 2.0"""
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        return signature
    
    def _get_headers(self, post_data):
        """Generate headers untuk request ke legacy endpoint"""
        signature = self._generate_signature(post_data)
        return {
            'Key': self.api_key,
            'Sign': signature,
            'Content-Type': 'application/x-www-form-urlencoded',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    
    def _get_headers_v2(self, query_string):
        """Generate headers untuk request ke Trade API 2.0"""
        signature = self._generate_signature_v2(query_string)
        return {
            'X-APIKEY': self.api_key,
            'Sign': signature,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    
    def get_balance(self):
        """Mendapatkan saldo akun"""
        post_data = {
            'method': 'getInfo',
            'nonce': self._next_nonce()
        }
        
        headers = self._get_headers(post_data)
        
        try:
            response = requests.post(
                self.base_url,
                data=post_data,
                headers=headers,
                timeout=30
            )
            result = response.json()
            
            if result.get('success') == 1:
                return result.get('return', {})
            else:
                return {'error': result.get('error', 'Unknown error')}
        except Exception as e:
            return {'error': str(e)}
    
    def get_ticker(self, pair='btcidr'):
        """Mendapatkan harga ticker"""
        try:
            pair_formatted = pair.lower()
            if '_' not in pair_formatted and pair_formatted.endswith('idr'):
                pair_formatted = pair_formatted[:-3] + '_idr'
                
            response = requests.get(
                f'{self.public_url}/ticker_all',
                headers=self.public_headers,
                timeout=30
            )
            result = response.json()
            
            if isinstance(result, dict) and 'tickers' in result:
                tickers = result['tickers']
                if pair_formatted in tickers:
                    # Return formatted as dictionary with last price to keep it compatible
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
    
    def get_orderbook(self, pair='btcidr'):
        """Mendapatkan order book"""
        try:
            pair_lower = pair.lower()
            response = requests.get(
                f'{self.public_url}/{pair_lower}/depth',
                headers=self.public_headers,
                timeout=30
            )
            result = response.json()
            
            if isinstance(result, dict) and ('buy' in result or 'sell' in result):
                return result
            else:
                return {'error': 'Invalid orderbook data'}
        except Exception as e:
            return {'error': str(e)}
    
    def buy(self, pair='btcidr', price=0, amount=0):
        """
        Melakukan order beli
        price: Harga per unit (jika 0, akan menggunakan market price)
        amount: Jumlah yang ingin dibeli (dalam IDR jika price=0, atau dalam crypto jika price>0)
        """
        coin = pair.lower().replace('_', '').replace('idr', '')
        post_data = {
            'method': 'trade',
            'pair': self._normalize_pair(pair),
            'type': 'buy',
            'price': str(price),
            'nonce': self._next_nonce()
        }
        if float(price) == 0:
            target_idr = max(float(amount), 10100.0)
            post_data['idr'] = str(int(target_idr))
        else:
            price_val = float(price)
            amount_val = float(amount)
            if price_val * amount_val < 10000.0:
                amount_val = round(10100.0 / price_val, 8)
                if price_val * amount_val < 10000.0:
                    amount_val += 0.00000001
            
            idr_val = int(price_val * amount_val)
            amount_str = f"{amount_val:.8f}".rstrip('0').rstrip('.')
            if '.' in amount_str:
                parts = amount_str.split('.')
                if len(parts[1]) > 8:
                    amount_str = parts[0] + '.' + parts[1][:8]
            post_data['idr'] = str(idr_val)
            post_data['amount'] = amount_str
            post_data[coin] = amount_str
        
        headers = self._get_headers(post_data)
        
        try:
            response = requests.post(
                self.base_url,
                data=post_data,
                headers=headers,
                timeout=30
            )
            try:
                result = response.json()
            except Exception as json_err:
                return {'error': f'JSON parse error: {json_err}. Status code: {response.status_code}. Response: {response.text[:200]}'}
            
            if result.get('success') == 1:
                return result.get('return', {})
            else:
                return {'error': result.get('error', 'Unknown error')}
        except Exception as e:
            return {'error': str(e)}
    
    def buy_market(self, pair='btcidr', amount_idr=0):
        """
        Melakukan order beli dengan market price
        amount_idr: Jumlah dalam IDR yang ingin dibelanjakan
        """
        # Dapatkan harga market terbaru
        ticker = self.get_ticker(pair)
        if 'error' in ticker:
            return ticker
        
        # Ambil harga jual terendah (best ask) dari ticker
        market_price = float(ticker.get('sell', 0))
        
        # Fallback ke orderbook jika ticker tidak punya data
        if market_price <= 0:
            orderbook = self.get_orderbook(pair)
            if not isinstance(orderbook, dict) or 'error' not in orderbook and orderbook.get('sell') and len(orderbook['sell']) > 0:
                market_price = float(orderbook['sell'][0][0])
            else:
                market_price = float(ticker.get('last', 0)) or float(ticker.get('low', 0))
        
        if market_price <= 0:
            return {'error': 'Invalid market price'}
        
        # Ensure the buy value is at least 10,100 IDR to bypass Indodax 10,000 IDR minimum limit safely
        target_idr = max(float(amount_idr), 10100.0)
        
        # Hitung jumlah crypto yang akan dibeli
        crypto_amount = round(target_idr / market_price, 8)
        
        # Lakukan order beli dengan harga market + sedikit markup untuk memastikan terisi
        buy_price = int(market_price * 1.001)  # 0.1% di atas market
        
        # Double check transaction value at buy_price
        if crypto_amount * buy_price < 10000:
            crypto_amount = round(10100.0 / buy_price, 8)
            if crypto_amount * buy_price < 10000:
                crypto_amount += 0.00000001
        
        return self.buy(pair, buy_price, crypto_amount)
    
    def sell(self, pair='btcidr', price=0, amount=0):
        """Melakukan order jual"""
        coin = pair.lower().replace('_', '').replace('idr', '')
        post_data = {
            'method': 'trade',
            'pair': self._normalize_pair(pair),
            'type': 'sell',
            'price': str(price),
            'nonce': self._next_nonce()
        }
        amount_str = f"{float(amount):.8f}".rstrip('0').rstrip('.')
        if '.' in amount_str:
            parts = amount_str.split('.')
            if len(parts[1]) > 8:
                amount_str = parts[0] + '.' + parts[1][:8]
        post_data['amount'] = amount_str
        post_data[coin] = amount_str
        headers = self._get_headers(post_data)
        try:
            response = requests.post(self.base_url, data=post_data, headers=headers, timeout=30)
            result = response.json()
            if result.get('success') == 1:
                return result.get('return', {})
            else:
                return {'error': result.get('error', 'Unknown error')}
        except Exception as e:
            return {'error': str(e)}
    
    def sell_market(self, pair='btcidr', crypto_amount=0):
        """Melakukan order jual dengan market price"""
        ticker = self.get_ticker(pair)
        if 'error' in ticker:
            return ticker
            
        # Ambil harga beli tertinggi (best bid) dari ticker
        market_price = float(ticker.get('buy', 0))
        
        # Fallback ke orderbook jika ticker tidak punya data
        if market_price <= 0:
            orderbook = self.get_orderbook(pair)
            if not isinstance(orderbook, dict) or 'error' not in orderbook and orderbook.get('buy') and len(orderbook['buy']) > 0:
                market_price = float(orderbook['buy'][0][0])
            else:
                market_price = float(ticker.get('last', 0)) or float(ticker.get('high', 0))
                
        if market_price <= 0:
            return {'error': 'Invalid market price'}
            
        sell_price = int(market_price * 0.999)  # 0.1% di bawah market
        return self.sell(pair, sell_price, crypto_amount)
    
    def get_order_status(self, pair='btcidr', order_id=''):
        """Mendapatkan status order"""
        post_data = {
            'method': 'getOrder',
            'pair': self._normalize_pair(pair),
            'order_id': order_id,
            'nonce': self._next_nonce()
        }
        
        headers = self._get_headers(post_data)
        
        try:
            response = requests.post(
                self.base_url,
                data=post_data,
                headers=headers,
                timeout=30
            )
            result = response.json()
            
            if result.get('success') == 1:
                ret = result.get('return', {})
                if 'order' in ret:
                    return ret['order']
                return ret
            else:
                return {'error': result.get('error', 'Unknown error')}
        except Exception as e:
            return {'error': str(e)}
    
    def get_trade_history(self, pair='btcidr', limit=10):
        """Mendapatkan riwayat trading"""
        post_data = {
            'method': 'tradeHistory',
            'pair': self._normalize_pair(pair),
            'nonce': self._next_nonce()
        }
        
        headers = self._get_headers(post_data)
        
        try:
            response = requests.post(
                self.base_url,
                data=post_data,
                headers=headers,
                timeout=30
            )
            result = response.json()
            
            if result.get('success') == 1:
                return_value = result.get('return', [])
                # Indodax returns {trades: [{data}, ...]} as dict containing a list
                if isinstance(return_value, dict):
                    trades = return_value.get('trades', [])
                    if isinstance(trades, list):
                        return trades
                    return []
                elif isinstance(return_value, list):
                    return return_value
                # Fallback
                return []
            else:
                return {'error': result.get('error', 'Unknown error')}
        except Exception as e:
            return {'error': str(e)}
    
    def get_ohlc(self, pair='btcidr', timeframe='1h', limit=100):
        """
        Mendapatkan data OHLC (Open, High, Low, Close) untuk candlestick chart
        Menggunakan endpoint resmi: /tradingview/history_v2
        
        timeframe: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w
        limit: jumlah candle (max 1000)
        """
        # Map timeframe ke format Indodax
        tf_map = {
            '1m': '1', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360', 
            '12h': '720', '1d': '1D', '3d': '3D', '1w': '1W'
        }
        tf = tf_map.get(timeframe, '60')  # Default to 1 hour
        
        try:
            # Hitung timestamp range
            now = int(time.time())
            # Estimasi durasi berdasarkan timeframe (dalam detik)
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
                    'symbol': pair.upper(),  # Harus uppercase sesuai dokumentasi
                    'tf': tf,
                    'from': from_time,
                    'to': now
                },
                timeout=30
            )
            result = response.json()
            
            if isinstance(result, list):
                # Format response: [{"Time": timestamp, "Open": float, "High": float, "Low": float, "Close": float, "Volume": float}]
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
            else:
                return {'error': result.get('message', 'Unknown error')}
        except Exception as e:
            return {'error': str(e)}
    
    # ==========================================
    # Trade API 2.0 Methods (New Endpoints)
    # ==========================================
    
    def get_trade_history_v2(self, pair='btcidr', limit=500, start_time=None, end_time=None):
        """
        Mendapatkan riwayat trading menggunakan Trade API 2.0
        Endpoint baru yang menggantikan method transactions di /tapi
        
        Parameters:
        - pair: Trading pair symbol (e.g., btcidr, ethidr)
        - limit: Jumlah data (10-1000, default 500)
        - start_time: Start timestamp (milliseconds)
        - end_time: End timestamp (milliseconds)
        """
        timestamp = int(time.time() * 1000)  # Current time in milliseconds
        recv_window = 5000  # Default 5 seconds
        
        # Build query string
        params = {
            'symbol': self._normalize_pair(pair),
            'limit': min(max(limit, 10), 1000),  # Clamp between 10-1000
            'timestamp': timestamp,
            'recvWindow': recv_window
        }
        
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time
        
        # Build query string for signature
        query_string = '&'.join([f"{key}={value}" for key, value in params.items()])
        
        headers = self._get_headers_v2(query_string)
        
        try:
            response = requests.get(
                f'{self.trade_api_url}/api/v2/myTrades',
                params=params,
                headers=headers,
                timeout=30
            )
            result = response.json()
            
            if 'data' in result:
                return result['data']
            else:
                return {'error': result.get('error', 'Unknown error'), 'code': result.get('code', 0)}
        except Exception as e:
            return {'error': str(e)}
    
    def get_order_history_v2(self, pair='btcidr', limit=100, start_time=None, end_time=None, sort='desc'):
        """
        Mendapatkan riwayat order menggunakan Trade API 2.0
        Endpoint baru yang menggantikan orderHistory di /tapi
        
        Parameters:
        - pair: Trading pair symbol (e.g., btcidr, ethidr)
        - limit: Jumlah data (10-1000, default 100)
        - start_time: Start timestamp (milliseconds)
        - end_time: End timestamp (milliseconds)
        - sort: Sorting order (asc or desc, default desc)
        """
        timestamp = int(time.time() * 1000)  # Current time in milliseconds
        recv_window = 5000  # Default 5 seconds
        
        # Build query string
        params = {
            'symbol': self._normalize_pair(pair),
            'limit': min(max(limit, 10), 1000),  # Clamp between 10-1000
            'timestamp': timestamp,
            'recvWindow': recv_window,
            'sort': sort
        }
        
        if start_time:
            params['startTime'] = start_time
        if end_time:
            params['endTime'] = end_time
        
        # Build query string for signature
        query_string = '&'.join([f"{key}={value}" for key, value in params.items()])
        
        headers = self._get_headers_v2(query_string)
        
        try:
            response = requests.get(
                f'{self.trade_api_url}/api/v2/order/histories',
                params=params,
                headers=headers,
                timeout=30
            )
            result = response.json()
            
            if 'data' in result:
                return result['data']
            else:
                return {'error': result.get('error', 'Unknown error'), 'code': result.get('code', 0)}
        except Exception as e:
            return {'error': str(e)}
    
    def cancel_order(self, pair='btcidr', order_id='', order_type=''):
        """
        Membatalkan order yang belum terisi
        """
        post_data = {
            'method': 'cancelOrder',
            'pair': self._normalize_pair(pair),
            'order_id': order_id,
            'type': order_type,
            'nonce': self._next_nonce()
        }
        
        headers = self._get_headers(post_data)
        
        try:
            response = requests.post(
                self.base_url,
                data=post_data,
                headers=headers,
                timeout=30
            )
            result = response.json()
            
            if result.get('success') == 1:
                return result.get('return', {})
            else:
                return {'error': result.get('error', 'Unknown error')}
        except Exception as e:
            return {'error': str(e)}
    
    def get_open_orders(self, pair='btcidr'):
        """
        Mendapatkan daftar order yang masih terbuka
        """
        post_data = {
            'method': 'openOrders',
            'pair': self._normalize_pair(pair),
            'nonce': self._next_nonce()
        }
        
        headers = self._get_headers(post_data)
        
        try:
            response = requests.post(
                self.base_url,
                data=post_data,
                headers=headers,
                timeout=30
            )
            result = response.json()
            
            if result.get('success') == 1:
                return_value = result.get('return', {})
                # Indodax returns {orders: {order_id: {data}}} as dict object
                if isinstance(return_value, dict):
                    orders: Any = return_value.get('orders', {})
                    orders_list: list[dict[str, Any]] = []
                    
                    if isinstance(orders, dict):
                        for _order_id in orders:
                            o = orders[_order_id]
                            o['order_id'] = str(_order_id)
                            orders_list.append(o)
                    elif isinstance(orders, list):
                        for o in orders:
                            orders_list.append(o)
                            
                    for o in orders_list:
                        # Extract amount dynamically
                        amount_val = 0.0
                        remain_val = 0.0
                        for k, v in o.items():
                            if k.startswith('order_') and k not in ['order_type', 'order_id']:
                                try:
                                    amount_val = float(v)
                                except ValueError:
                                    pass
                            elif k.startswith('remain_'):
                                try:
                                    remain_val = float(v)
                                except ValueError:
                                    pass
                        o['amount'] = amount_val
                        o['amount_remaining'] = remain_val
                        
                    return orders_list
                elif isinstance(return_value, list):
                    return return_value
                # Fallback: return empty list
                return []
            else:
                return {'error': result.get('error', 'Unknown error')}
        except Exception as e:
            return {'error': str(e)}
    
    # ==========================================
    # Additional Public API Methods
    # ==========================================
    
    def get_server_time(self):
        """Mendapatkan waktu server Indodax"""
        try:
            response = requests.get(
                f'{self.public_url}/server_time',
                headers=self.public_headers,
                timeout=30
            )
            return response.json()
        except Exception as e:
            return {'error': str(e)}
    
    def get_pairs(self):
        """Mendapatkan daftar pair yang tersedia"""
        try:
            response = requests.get(
                f'{self.public_url}/pairs',
                headers=self.public_headers,
                timeout=30
            )
            return response.json()
        except Exception as e:
            return {'error': str(e)}
    
    def get_ticker_all(self):
        """Mendapatkan ticker untuk semua pair"""
        try:
            response = requests.get(
                f'{self.public_url}/ticker_all',
                headers=self.public_headers,
                timeout=30
            )
            return response.json()
        except Exception as e:
            return {'error': str(e)}
    
    def get_price_increments(self):
        """Mendapatkan price increments untuk setiap pair"""
        try:
            response = requests.get(
                f'{self.public_url}/price_increments',
                headers=self.public_headers,
                timeout=30
            )
            return response.json()
        except Exception as e:
            return {'error': str(e)}
    
    def get_summaries(self):
        """Mendapatkan summary untuk semua pair"""
        try:
            response = requests.get(
                f'{self.public_url}/summaries',
                headers=self.public_headers,
                timeout=30
            )
            return response.json()
        except Exception as e:
            return {'error': str(e)}
    
    def get_public_trades(self, pair='btcidr'):
        """Mendapatkan riwayat transaksi publik untuk sebuah pair"""
        try:
            response = requests.get(
                f'{self.public_url}/trades/{pair.lower()}',
                headers=self.public_headers,
                timeout=30
            )
            return response.json()
        except Exception as e:
            return {'error': str(e)}
