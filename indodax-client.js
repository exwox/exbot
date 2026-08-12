/**
 * Indodax API Client - Node.js Version
 */
const axios = require('axios');
const crypto = require('crypto');

class IndodaxClient {
    static _nonceCounter = Date.now();

    constructor(apiKey, secretKey) {
        this.apiKey = apiKey;
        this.secretKey = secretKey;
        this.publicUrl = 'https://indodax.com/api';
        this.base_url = 'https://indodax.com/tapi';
    }

    _normalizePair(pair) {
        if (!pair) return 'btc_idr';
        let p = String(pair).trim().toLowerCase().replace(/[\/\-\s]/g, '');
        if (!p.includes('_')) {
            if (p.endsWith('idr') && p.length > 3) {
                p = p.slice(0, -3) + '_idr';
            } else if (p.endsWith('usdt') && p.length > 4) {
                p = p.slice(0, -4) + '_usdt';
            }
        }
        return p;
    }

    static _nextNonce() {
        const now = Date.now();
        if (now > IndodaxClient._nonceCounter) {
            IndodaxClient._nonceCounter = now;
        } else {
            IndodaxClient._nonceCounter++;
        }
        return IndodaxClient._nonceCounter;
    }

    async get_ticker(pair = 'btcidr') {
        try {
            const response = await axios.get(`${this.publicUrl}/ticker_all`, { timeout: 30000 });
const result = response.data;

            if (result && result.tickers) {
                let pairFormatted = pair.toLowerCase();
                const idrIndex = pairFormatted.lastIndexOf('idr');
                if (idrIndex > 0) {
                    pairFormatted = pairFormatted.slice(0, idrIndex) + '_' + pairFormatted.slice(idrIndex);
                }

                const tickerData = result.tickers[pairFormatted];

                if (tickerData && tickerData.last) {
                    return { last: tickerData.last };
                } else {
                    const availablePairs = Object.keys(result.tickers).slice(0, 10).join(', ');
                    return { error: `Ticker not found for pair: ${pair} (formatted as: ${pairFormatted}). Sample available: ${availablePairs}...` };
                }
            } else {
                return { error: result.message || 'Unknown error' };
            }
        } catch (err) {
            return { error: err.message };
        }
    }

    async get_ticker_all() {
        try {
            const response = await axios.get(`${this.publicUrl}/ticker_all`, { timeout: 30000 });
            return response.data;
        } catch (err) {
            return { error: err.message };
        }
    }

    async get_trade_history(pair = 'btcidr', limit = 50) {
        const postData = {
            method: 'tradeHistory',
            pair: this._normalizePair(pair),
            nonce: IndodaxClient._nextNonce()
        };

        const postQuery = Object.entries(postData)
            .map(([key, value]) => `${key}=${value}`)
            .join('&');

        const signature = crypto
            .createHmac('sha512', this.secretKey)
            .update(postQuery)
            .digest('hex');

        try {
            const response = await axios.post(this.base_url, postQuery, {
                headers: {
                    'Key': this.apiKey,
                    'Sign': signature,
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                timeout: 30000
            });

            const result = response.data;
            if (result.success === 1) {
                const returnValue = result.return || [];
                if (typeof returnValue === 'object' && !Array.isArray(returnValue)) {
                    const trades = returnValue.trades || [];
                    return Array.isArray(trades) ? trades : [];
                }
                return Array.isArray(returnValue) ? returnValue : [];
            } else {
                return { error: result.error || 'Unknown error' };
            }
        } catch (err) {
            return { error: err.message };
        }
    }

    async get_open_orders(pair = 'btcidr') {
        const postData = {
            method: 'openOrders',
            pair: this._normalizePair(pair),
            nonce: IndodaxClient._nextNonce()
        };

        const postQuery = Object.entries(postData)
            .map(([key, value]) => `${key}=${value}`)
            .join('&');

        const signature = crypto
            .createHmac('sha512', this.secretKey)
            .update(postQuery)
            .digest('hex');

        try {
            const response = await axios.post(this.base_url, postQuery, {
                headers: {
                    'Key': this.apiKey,
                    'Sign': signature,
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                timeout: 30000
            });

            const result = response.data;
            if (result.success === 1) {
                const returnValue = result.return || {};
                if (typeof returnValue === 'object' && !Array.isArray(returnValue)) {
                    const orders = returnValue.orders || {};
                    let rawOrders = [];
                    if (typeof orders === 'object' && !Array.isArray(orders)) {
                        rawOrders = Object.keys(orders).map(orderId => ({
                            ...orders[orderId],
                            order_id: orderId
                        }));
                    } else if (Array.isArray(orders)) {
                        rawOrders = orders;
                    }

                    return rawOrders.map(o => {
                        const coin = this._normalizePair(pair).split('_')[0];
                        const numberValue = value => {
                            const parsed = parseFloat(value || 0);
                            return Number.isFinite(parsed) ? parsed : 0;
                        };
                        // `order_idr` is quote currency, not a crypto
                        // quantity. Keep both values separate so the UI does
                        // not multiply an IDR amount by price a second time.
                        const amount = numberValue(o[`order_${coin}`]);
                        const amount_remaining = numberValue(o[`remain_${coin}`]);
                        return {
                            ...o,
                            order_id: o.order_id || '',
                            amount: amount,
                            amount_remaining: amount_remaining,
                            amount_idr: numberValue(o.order_idr || o.order_rp),
                            amount_remaining_idr: numberValue(o.remain_idr || o.remain_rp)
                        };
                    });
                }
                return Array.isArray(returnValue) ? returnValue : [];
            } else {
                return { error: result.error || 'Unknown error' };
            }
        } catch (err) {
            return { error: err.message };
        }
    }

    async get_balance() {
        const postData = {
            method: 'getInfo',
            nonce: IndodaxClient._nextNonce()
        };

        const postQuery = Object.entries(postData)
            .map(([key, value]) => `${key}=${value}`)
            .join('&');

        const signature = crypto
            .createHmac('sha512', this.secretKey)
            .update(postQuery)
            .digest('hex');

        try {
            const response = await axios.post(this.base_url, postQuery, {
                headers: {
                    'Key': this.apiKey,
                    'Sign': signature,
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                timeout: 30000
            });

            const result = response.data;
            if (result.success === 1) {
                return result.return || {};
            } else {
                return { error: result.error || 'Unknown error' };
            }
        } catch (err) {
            return { error: err.message };
        }
    }

    async buy_market(pair = 'btcidr', amount_idr = 0) {
        const ticker = await this.get_ticker(pair);
        if (ticker.error) {
            return ticker;
        }

        try {
            let pairDepth = pair.toLowerCase();
            if (!pairDepth.includes('_')) {
                const idx = pairDepth.lastIndexOf('idr');
                if (idx > 0) {
                    pairDepth = pairDepth.substring(0, idx) + '_' + pairDepth.substring(idx);
                }
            }

            let orderbook = null;
            try {
                const response = await axios.get(`${this.publicUrl}/${pairDepth}/depth`, { timeout: 30000 });
                orderbook = response.data;
            } catch (depthErr) {
                console.warn('[IndodaxClient] Gagal mengambil orderbook depth, fallback menggunakan ticker.last:', depthErr.message);
            }

            let marketPrice = 0;
            if (orderbook && orderbook.sell && orderbook.sell.length > 0) {
                marketPrice = parseFloat(orderbook.sell[0][0]);
            } else {
                marketPrice = parseFloat(ticker.last || 0);
            }

            if (marketPrice <= 0) {
                return { error: 'Invalid market price' };
            }

            const buyPrice = Math.ceil(marketPrice * 1.001);
            const cryptoAmount = amount_idr / buyPrice;

            const postData = {
                method: 'trade',
                pair: this._normalizePair(pair),
                type: 'buy',
                price: String(buyPrice),
                idr: String(amount_idr),
                nonce: IndodaxClient._nextNonce()
            };

            const postQuery = Object.entries(postData)
                .map(([key, value]) => `${key}=${value}`)
                .join('&');

            const signature = crypto
                .createHmac('sha512', this.secretKey)
                .update(postQuery)
                .digest('hex');

            const resultResponse = await axios.post(this.base_url, postQuery, {
                headers: {
                    'Key': this.apiKey,
                    'Sign': signature,
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                timeout: 30000
            });

            const result = resultResponse.data;
            if (result.success === 1) {
                return result.return || {};
            } else {
                return { error: result.error || 'Unknown error' };
            }
        } catch (err) {
            let errorMsg = err.message;
            if (err.response) {
                const status = err.response.status;
                const body = err.response.data;
                if (typeof body === 'object' && body && body.error) {
                    errorMsg = `[HTTP ${status}] ${body.error}`;
                } else if (typeof body === 'string') {
                    errorMsg = `[HTTP ${status}] Indodax mengembalikan HTML. Cek: API key valid, nonce benar, IP tidak diblokir.`;
                } else {
                    errorMsg = `[HTTP ${status}] ${err.message}`;
                }
            }
            return { error: errorMsg };
        }
    }

    async get_trade_history_v2(pair = 'btcidr', limit = 500) {
        const timestamp = Date.now();
        const recvWindow = 5000;
        const endTime = timestamp;
        const startTime = endTime - (7 * 24 * 60 * 60 * 1000);

        const params = {
            symbol: pair,
            limit: Math.min(Math.max(limit, 10), 1000),
            timestamp: timestamp,
            recvWindow: recvWindow,
            startTime: startTime,
            endTime: endTime
        };

        const queryString = Object.entries(params)
            .map(([key, value]) => `${key}=${value}`)
            .join('&');

        const signature = crypto
            .createHmac('sha512', this.secretKey)
            .update(queryString)
            .digest('hex');

        try {
            const response = await axios.get('https://tapi.indodax.com/api/v2/myTrades', {
                params: params,
                headers: {
                    'X-APIKEY': this.apiKey,
                    'Sign': signature,
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                timeout: 30000
            });

            const result = response.data;
            if (result.data && Array.isArray(result.data)) {
                return result.data;
            } else if (result.code) {
                return { error: result.error || result.msg || 'Unknown error', code: result.code };
            } else {
                return { error: 'Unexpected response format' };
            }
        } catch (err) {
            const errorDetail = err.response ? JSON.stringify(err.response.data).substring(0, 300) : err.message;
            return { error: errorDetail };
        }
    }

    async get_ohlc(pair = 'btcidr', timeframe = '1h', limit = 100) {
        const tfMap = {
            '1m': '1', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360',
            '12h': '720', '1d': '1D', '3d': '3D', '1w': '1W'
        };
        const tf = tfMap[timeframe] || '60';

        const now = Math.floor(Date.now() / 1000);
        const durationMap = {
            '1': 60, '5': 300, '15': 900, '30': 1800,
            '60': 3600, '120': 7200, '240': 14400, '360': 21600,
            '720': 43200, '1D': 86400, '3D': 259200, '1W': 604800
        };
        const duration = (durationMap[tf] || 3600) * limit;
        const fromTime = now - duration;

        try {
            const response = await axios.get('https://indodax.com/tradingview/history_v2', {
                params: {
                    symbol: pair.toUpperCase(),
                    tf: tf,
                    from: fromTime,
                    to: now
                },
                timeout: 30000
            });

            const result = response.data;
            if (Array.isArray(result)) {
                return result.map(candle => ({
                    timestamp: candle.Time || 0,
                    open: parseFloat(candle.Open || 0),
                    high: parseFloat(candle.High || 0),
                    low: parseFloat(candle.Low || 0),
                    close: parseFloat(candle.Close || 0),
                    volume: parseFloat(candle.Volume || 0)
                }));
            } else {
                return { error: result.message || 'Unknown error' };
            }
        } catch (err) {
            return { error: err.message };
        }
    }

    async sell(pair, price, amount) {
        const postData = {
            method: 'trade',
            pair: this._normalizePair(pair),
            type: 'sell',
            price: String(price),
            idr: String(amount * price),
            nonce: IndodaxClient._nextNonce()
        };

        const postQuery = Object.entries(postData)
            .map(([key, value]) => `${key}=${value}`)
            .join('&');

        const signature = crypto
            .createHmac('sha512', this.secretKey)
            .update(postQuery)
            .digest('hex');

        try {
            const response = await axios.post(this.base_url, postQuery, {
                headers: {
                    'Key': this.apiKey,
                    'Sign': signature,
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                timeout: 30000
            });

            const result = response.data;
            if (result.success === 1) {
                return result.return || {};
            } else {
                return { error: result.error || 'Unknown error' };
            }
        } catch (err) {
            return { error: err.message };
        }
    }

    async sell_market(pair, amount) {
        const ticker = await this.get_ticker(pair);
        if (ticker.error) {
            return ticker;
        }

        const price = parseFloat(ticker.last || 0);
        if (price <= 0) {
            return { error: 'Invalid market price' };
        }

        return await this.sell(pair, price, amount);
    }

    async cancel_order(pair, orderId, orderType) {
        const postData = {
            method: 'cancelOrder',
            pair: this._normalizePair(pair),
            order_id: orderId,
            type: orderType,
            nonce: IndodaxClient._nextNonce()
        };

        const postQuery = Object.entries(postData)
            .map(([key, value]) => `${key}=${value}`)
            .join('&');

        const signature = crypto
            .createHmac('sha512', this.secretKey)
            .update(postQuery)
            .digest('hex');

        try {
            const response = await axios.post(this.base_url, postQuery, {
                headers: {
                    'Key': this.apiKey,
                    'Sign': signature,
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                timeout: 30000
            });

            const result = response.data;
            if (result.success === 1) {
                return { success: true };
            } else {
                return { error: result.error || 'Unknown error' };
            }
        } catch (err) {
            return { error: err.message };
        }
    }

    async cancel_order_by_client_id(clientOrderId) {
        const postData = {
            method: 'cancelByClientOrderId',
            client_order_id: String(clientOrderId),
            nonce: IndodaxClient._nextNonce()
        };
        const postQuery = Object.entries(postData)
            .map(([key, value]) => `${key}=${value}`)
            .join('&');
        const signature = crypto.createHmac('sha512', this.secretKey)
            .update(postQuery).digest('hex');
        try {
            const response = await axios.post(this.base_url, postQuery, {
                headers: {
                    'Key': this.apiKey,
                    'Sign': signature,
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                timeout: 30000
            });
            const result = response.data;
            return result.success === 1
                ? { success: true, ...(result.return || {}) }
                : { error: result.error || 'Unknown error' };
        } catch (err) {
            return { error: err.message };
        }
    }
}

module.exports = { IndodaxClient };
