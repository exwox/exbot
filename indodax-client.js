/**
 * Indodax API Client - Node.js Version
 *
 * Private API v1 and v2 use different credentials and wire protocols. The
 * selected version is immutable for this client; there is no cross-version
 * retry or fallback.
 */
const axios = require('axios');
const crypto = require('crypto');

const API_TIMEOUT_MS = 30000;
const V2_RECV_WINDOW_MS = 5000;
const V2_MAX_HISTORY_RANGE_MS = 7 * 24 * 60 * 60 * 1000;

class IndodaxClient {
    static _nonceCounter = Date.now();

    constructor(apiKey, secretKey, apiVersion = 'v1') {
        const requestedVersion = (
            apiVersion && typeof apiVersion === 'object'
                ? apiVersion.api_version || apiVersion.apiVersion
                : apiVersion
        ) || 'v1';
        const normalizedVersion = String(requestedVersion).trim().toLowerCase();
        if (!['v1', 'v2'].includes(normalizedVersion)) {
            throw new Error(`Unsupported Indodax API version: ${requestedVersion}`);
        }

        this.apiKey = apiKey || '';
        this.secretKey = secretKey || '';
        this.apiVersion = normalizedVersion;
        this.api_version = normalizedVersion;
        this.publicUrl = 'https://indodax.com/api';
        this.v1BaseUrl = 'https://indodax.com/tapi';
        this.v2BaseUrl = 'https://api.indodax.com';
        this.base_url = normalizedVersion === 'v2'
            ? this.v2BaseUrl
            : this.v1BaseUrl;
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

    _normalizePair(pair) {
        if (!pair) return 'btc_idr';
        let normalized = String(pair).trim().toLowerCase()
            .replace(/[\/\-\s]/g, '');
        if (!normalized.includes('_')) {
            if (normalized.endsWith('idr') && normalized.length > 3) {
                normalized = `${normalized.slice(0, -3)}_idr`;
            } else if (normalized.endsWith('usdt') && normalized.length > 4) {
                normalized = `${normalized.slice(0, -4)}_usdt`;
            }
        }
        return normalized;
    }

    _normalizeSymbol(pair) {
        const symbol = String(pair || 'btcidr').trim().toUpperCase()
            .replace(/[\/_\-\s]/g, '');
        if (!/^[A-Z0-9]+$/.test(symbol)) {
            throw new Error(`Invalid Indodax symbol: ${pair}`);
        }
        return symbol;
    }

    _baseAsset(pair) {
        return this._normalizePair(pair).split('_', 1)[0];
    }

    _number(value) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : 0;
    }

    _decimalString(value) {
        const input = String(value ?? '').trim();
        if (!/^[+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(input)) {
            return null;
        }
        if (!/[eE]/.test(input)) return input.replace(/^\+/, '');

        const [coefficient, exponentText] = input.toLowerCase()
            .replace(/^\+/, '').split('e');
        const exponent = Number(exponentText);
        const [integerPart, fractionPart = ''] = coefficient.split('.');
        const digits = `${integerPart}${fractionPart}`;
        const decimalIndex = integerPart.length + exponent;
        if (decimalIndex <= 0) {
            return `0.${'0'.repeat(-decimalIndex)}${digits}`;
        }
        if (decimalIndex >= digits.length) {
            return `${digits}${'0'.repeat(decimalIndex - digits.length)}`;
        }
        return `${digits.slice(0, decimalIndex)}.${digits.slice(decimalIndex)}`;
    }

    _encodeParams(params) {
        const encoded = new URLSearchParams();
        for (const [key, value] of Object.entries(params || {})) {
            if (value === undefined || value === null || value === '') continue;
            encoded.append(key, String(value));
        }
        return encoded.toString();
    }

    _formatHttpError(error, fallback = 'Indodax API request failed') {
        const response = error && error.response;
        const body = response && response.data;
        let message = error && error.message ? error.message : fallback;
        let code;
        if (body && typeof body === 'object') {
            message = body.msg || body.error || body.message || message;
            code = body.code ?? body.error_code;
        } else if (typeof body === 'string' && body.trim()) {
            message = body.trim().slice(0, 300);
        }

        const result = { error: String(message || fallback) };
        if (code !== undefined && code !== null && code !== '') result.code = code;
        if (response && response.status) result.http_status = response.status;
        return result;
    }

    _versionError(method, expectedVersion) {
        return {
            error: `${method} requires Indodax API ${expectedVersion}; selected version is ${this.apiVersion}`,
            code: 'API_VERSION_MISMATCH'
        };
    }

    async _requestV1(method, params = {}) {
        const payload = {
            method,
            nonce: IndodaxClient._nextNonce(),
            ...params
        };
        const body = this._encodeParams(payload);
        const signature = crypto.createHmac('sha512', this.secretKey)
            .update(body).digest('hex');
        try {
            const response = await axios.post(this.v1BaseUrl, body, {
                headers: {
                    Key: this.apiKey,
                    Sign: signature,
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                timeout: API_TIMEOUT_MS
            });
            const result = response.data;
            if (result && Number(result.success) === 1) {
                return result.return ?? {};
            }
            return {
                error: result?.error || result?.message || 'Unknown Indodax v1 error',
                ...(result?.error_code ? { code: result.error_code } : {})
            };
        } catch (error) {
            return this._formatHttpError(error, 'Indodax v1 request failed');
        }
    }

    async _requestV2(httpMethod, path, params = {}) {
        const signedParams = {
            ...params,
            timestamp: Date.now(),
            recvWindow: V2_RECV_WINDOW_MS
        };
        const encoded = this._encodeParams(signedParams);
        const signature = crypto.createHmac('sha256', this.secretKey)
            .update(encoded).digest('hex');
        const method = String(httpMethod).toUpperCase();
        const headers = {
            'X-APIKEY': this.apiKey,
            Sign: signature,
            Accept: 'application/json'
        };
        try {
            let response;
            if (method === 'POST') {
                response = await axios.post(`${this.v2BaseUrl}${path}`, encoded, {
                    headers: {
                        ...headers,
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    timeout: API_TIMEOUT_MS
                });
            } else {
                const url = `${this.v2BaseUrl}${path}${encoded ? `?${encoded}` : ''}`;
                if (method === 'GET') {
                    response = await axios.get(url, { headers, timeout: API_TIMEOUT_MS });
                } else if (method === 'DELETE') {
                    response = await axios.delete(url, { headers, timeout: API_TIMEOUT_MS });
                } else {
                    return { error: `Unsupported Indodax v2 HTTP method: ${method}` };
                }
            }

            const result = response.data;
            if (result && typeof result === 'object' &&
                result.code !== undefined && result.msg) {
                return {
                    error: result.msg,
                    code: result.code,
                    ...(response.status ? { http_status: response.status } : {})
                };
            }
            return result;
        } catch (error) {
            return this._formatHttpError(error, 'Indodax v2 request failed');
        }
    }

    _normalizeLegacyOrder(pair, order, fallbackOrderId = '') {
        const raw = order && typeof order === 'object' ? order : {};
        const coin = this._baseAsset(pair);
        const amount = this._number(
            raw.amount ?? raw[`order_${coin}`] ?? raw.order_amount
        );
        const amountRemaining = this._number(
            raw.amount_remaining ?? raw[`remain_${coin}`] ?? raw.remain_amount
        );
        const amountIdr = this._number(raw.amount_idr ?? raw.order_idr ?? raw.order_rp);
        const amountRemainingIdr = this._number(
            raw.amount_remaining_idr ?? raw.remain_idr ?? raw.remain_rp
        );
        const price = this._number(raw.price);
        let filledAmount = this._number(raw.filled_amount);
        if (!filledAmount && amount > 0 && amountRemaining >= 0) {
            filledAmount = Math.max(amount - amountRemaining, 0);
        }
        return {
            ...raw,
            order_id: String(raw.order_id ?? fallbackOrderId ?? ''),
            client_order_id: String(raw.client_order_id ?? ''),
            type: String(raw.type || raw.side || '').toLowerCase(),
            side: String(raw.side || raw.type || '').toLowerCase(),
            price,
            amount,
            amount_remaining: amountRemaining,
            amount_idr: amountIdr,
            amount_remaining_idr: amountRemainingIdr,
            filled_amount: filledAmount,
            filled_quote: this._number(raw.filled_quote) || filledAmount * price,
            status: String(raw.status || 'open').toLowerCase()
        };
    }

    _normalizeV2Status(status, originalQuantity, executedQuantity) {
        const normalized = String(status || '').toUpperCase();
        const mapping = {
            NEW: 'open',
            PARTIALLY_FILLED: 'partially_filled',
            FILLED: 'filled',
            CANCELLED: 'cancelled',
            CANCELED: 'cancelled',
            REJECTED: 'rejected'
        };
        if (mapping[normalized]) return mapping[normalized];
        if (executedQuantity > 0 && originalQuantity > 0 &&
            executedQuantity >= originalQuantity) return 'filled';
        if (executedQuantity > 0) return 'partially_filled';
        return 'open';
    }

    _normalizeV2Order(order, fallbackPair = '') {
        const raw = order && typeof order === 'object' ? order : {};
        const symbol = String(raw.symbol || this._normalizeSymbol(fallbackPair));
        const price = this._number(raw.price);
        const originalQuantity = this._number(
            raw.origQty ?? raw.oriQty ?? raw.quantity
        );
        const executedQuantity = this._number(raw.executedQty);
        const remainingQuantity = Math.max(originalQuantity - executedQuantity, 0);
        const side = String(raw.side || '').toLowerCase();
        const exchangeOrderType = String(raw.type || '').toLowerCase();
        const clientOrderId = raw.clientOrderId ?? raw.origClientOrderId ?? '';
        return {
            ...raw,
            order_id: String(raw.orderId ?? raw.fullOrderId ?? ''),
            full_order_id: String(raw.fullOrderId ?? ''),
            client_order_id: String(clientOrderId),
            pair: this._normalizePair(symbol),
            symbol,
            side,
            type: side,
            order_type: exchangeOrderType,
            exchange_order_type: exchangeOrderType,
            price,
            amount: originalQuantity,
            amount_remaining: remainingQuantity,
            amount_idr: price * originalQuantity,
            amount_remaining_idr: price * remainingQuantity,
            filled_amount: executedQuantity,
            filled_quote: price * executedQuantity,
            status: this._normalizeV2Status(
                raw.status, originalQuantity, executedQuantity
            ),
            time: raw.time ?? raw.submitTime ?? '',
            submit_time: raw.submitTime ?? raw.time ?? '',
            finish_time: raw.finishTime ?? ''
        };
    }

    _normalizeV2Trade(trade) {
        const raw = trade && typeof trade === 'object' ? trade : {};
        const side = raw.isBuyer === true ? 'buy' : 'sell';
        const quantity = this._number(raw.qty);
        const quoteQuantity = this._number(raw.quoteQty);
        return {
            ...raw,
            trade_id: String(raw.tradeId ?? ''),
            order_id: String(raw.orderId ?? ''),
            client_order_id: String(raw.clientOrderId ?? ''),
            pair: this._normalizePair(raw.symbol || ''),
            type: side,
            side,
            price: this._number(raw.price),
            amount: quantity,
            amount_crypto: quantity,
            amount_idr: quoteQuantity,
            quote_amount: quoteQuantity,
            fee: this._number(raw.commission),
            fee_asset: String(raw.commissionAsset || '').toLowerCase(),
            is_maker: raw.isMaker === true,
            trade_time: raw.time ?? '',
            time: raw.time ?? ''
        };
    }

    _validateClientOrderId(clientOrderId) {
        if (!clientOrderId) return null;
        const value = String(clientOrderId);
        if (value.length > 36 || !/^[A-Za-z0-9_-]+$/.test(value)) {
            return {
                error: 'client_order_id must contain only alphanumeric, underscore, or hyphen and be at most 36 characters'
            };
        }
        return null;
    }

    async test_connection() {
        return this.get_balance();
    }

    async get_ticker(pair = 'btcidr') {
        try {
            const response = await axios.get(`${this.publicUrl}/ticker_all`, {
                timeout: API_TIMEOUT_MS
            });
            const result = response.data;
            if (!result || !result.tickers) {
                return { error: result?.message || 'Unknown error' };
            }
            const pairFormatted = this._normalizePair(pair);
            const tickerData = result.tickers[pairFormatted];
            if (tickerData && tickerData.last) {
                return {
                    last: tickerData.last,
                    high: tickerData.high,
                    low: tickerData.low,
                    buy: tickerData.buy,
                    sell: tickerData.sell
                };
            }
            const availablePairs = Object.keys(result.tickers).slice(0, 10).join(', ');
            return {
                error: `Ticker not found for pair: ${pair} (formatted as: ${pairFormatted}). Sample available: ${availablePairs}...`
            };
        } catch (error) {
            return this._formatHttpError(error, 'Failed to fetch Indodax ticker');
        }
    }

    async get_ticker_all() {
        try {
            const response = await axios.get(`${this.publicUrl}/ticker_all`, {
                timeout: API_TIMEOUT_MS
            });
            return response.data;
        } catch (error) {
            return this._formatHttpError(error, 'Failed to fetch Indodax tickers');
        }
    }

    async get_balance() {
        if (this.apiVersion === 'v1') return this._requestV1('getInfo');

        const result = await this._requestV2('GET', '/api/v2/account', {
            omitZeroBalances: false
        });
        if (!result || result.error) return result;
        if (!Array.isArray(result.balances)) {
            return { error: 'Unexpected Indodax v2 account response' };
        }
        const balance = {};
        const balanceHold = {};
        for (const item of result.balances) {
            if (!item || !item.asset) continue;
            const asset = String(item.asset).toLowerCase();
            balance[asset] = item.free ?? '0';
            balanceHold[asset] = item.locked ?? '0';
        }
        return {
            ...result,
            balance,
            balance_hold: balanceHold,
            can_trade: result.canTrade === true,
            can_withdraw: result.canWithdraw === true,
            account_type: result.accountType,
            user_id: result.uid
        };
    }

    async get_open_orders(pair = 'btcidr') {
        if (this.apiVersion === 'v2') {
            const result = await this._requestV2('GET', '/api/v2/openOrders', {
                symbol: this._normalizeSymbol(pair)
            });
            if (!result || result.error) return result;
            if (!Array.isArray(result)) {
                return { error: 'Unexpected Indodax v2 open-orders response' };
            }
            return result.map(order => this._normalizeV2Order(order, pair));
        }

        const result = await this._requestV1('openOrders', {
            pair: this._normalizePair(pair)
        });
        if (!result || result.error) return result;
        const orders = result.orders ?? result;
        let rawOrders = [];
        if (Array.isArray(orders)) {
            rawOrders = orders;
        } else if (orders && typeof orders === 'object') {
            rawOrders = Object.entries(orders).map(([orderId, order]) => ({
                ...order,
                order_id: order.order_id || orderId
            }));
        }
        return rawOrders.map(order => this._normalizeLegacyOrder(pair, order));
    }

    async get_trade_history(pair = 'btcidr', limit = 50) {
        if (this.apiVersion === 'v2') return this._getTradeHistoryV2(pair, limit);
        const result = await this._requestV1('tradeHistory', {
            pair: this._normalizePair(pair),
            count: Math.min(Math.max(Number(limit) || 50, 1), 1000)
        });
        if (!result || result.error) return result;
        const trades = Array.isArray(result) ? result : result.trades;
        return Array.isArray(trades) ? trades : [];
    }

    async _getTradeHistoryV2(pair, limit = 500, options = {}) {
        const params = {
            symbol: this._normalizeSymbol(pair),
            limit: Math.min(Math.max(Number(limit) || 500, 10), 1000),
            sort: options.sort === 'asc' ? 'asc' : 'desc'
        };
        if (options.orderId) params.orderId = String(options.orderId);
        if (options.clientOrderId) params.clientOrderId = String(options.clientOrderId);
        if (options.startTime) params.startTime = Number(options.startTime);
        if (options.endTime) params.endTime = Number(options.endTime);
        const result = await this._requestV2('GET', '/api/v2/myTrades', params);
        if (!result || result.error) return result;
        const trades = Array.isArray(result) ? result : result.data;
        if (!Array.isArray(trades)) {
            return { error: 'Unexpected Indodax v2 trade-history response' };
        }
        return trades.map(trade => this._normalizeV2Trade(trade));
    }

    async get_trade_history_v2(
        pair = 'btcidr', limit = 500, startTime = null, endTime = null
    ) {
        if (this.apiVersion !== 'v2') {
            return this._versionError('get_trade_history_v2', 'v2');
        }
        const effectiveEnd = endTime || Date.now();
        const effectiveStart = startTime || (effectiveEnd - V2_MAX_HISTORY_RANGE_MS);
        return this._getTradeHistoryV2(pair, limit, {
            startTime: effectiveStart,
            endTime: effectiveEnd
        });
    }

    async get_order_history(pair = 'btcidr', limit = 100) {
        if (this.apiVersion === 'v2') return this._getOrderHistoryV2(pair, limit);
        const result = await this._requestV1('orderHistory', {
            pair: this._normalizePair(pair),
            count: Math.min(Math.max(Number(limit) || 100, 1), 1000)
        });
        if (!result || result.error) return result;
        const orders = Array.isArray(result) ? result : result.orders;
        return Array.isArray(orders)
            ? orders.map(order => this._normalizeLegacyOrder(pair, order))
            : [];
    }

    async _getOrderHistoryV2(pair, limit = 100, options = {}) {
        const params = {
            symbol: this._normalizeSymbol(pair),
            limit: Math.min(Math.max(Number(limit) || 100, 10), 1000),
            sort: options.sort === 'asc' ? 'asc' : 'desc'
        };
        if (options.startTime) params.startTime = Number(options.startTime);
        if (options.endTime) params.endTime = Number(options.endTime);
        const result = await this._requestV2(
            'GET', '/api/v2/order/histories', params
        );
        if (!result || result.error) return result;
        const orders = Array.isArray(result) ? result : result.data;
        if (!Array.isArray(orders)) {
            return { error: 'Unexpected Indodax v2 order-history response' };
        }
        return orders.map(order => this._normalizeV2Order(order, pair));
    }

    async get_order_history_v2(
        pair = 'btcidr', limit = 100, startTime = null, endTime = null,
        sort = 'desc'
    ) {
        if (this.apiVersion !== 'v2') {
            return this._versionError('get_order_history_v2', 'v2');
        }
        return this._getOrderHistoryV2(pair, limit, {
            ...(startTime ? { startTime } : {}),
            ...(endTime ? { endTime } : {}),
            sort
        });
    }

    async buy(pair = 'btcidr', price = 0, amount = 0, clientOrderId = '') {
        const clientIdError = this._validateClientOrderId(clientOrderId);
        if (clientIdError) return clientIdError;
        if (this.apiVersion === 'v2') {
            return this._createV2Order({
                pair, side: 'BUY', type: 'LIMIT', price,
                quantity: amount, clientOrderId
            });
        }
        const priceValue = this._decimalString(price);
        const amountValue = this._decimalString(amount);
        if (!priceValue || this._number(price) <= 0 ||
            !amountValue || this._number(amount) <= 0) {
            return { error: 'Limit buy requires positive price and amount' };
        }
        const params = {
            pair: this._normalizePair(pair),
            type: 'buy',
            order_type: 'limit',
            price: priceValue,
            [this._baseAsset(pair)]: amountValue
        };
        if (clientOrderId) params.client_order_id = String(clientOrderId);
        return this._requestV1('trade', params);
    }

    async buy_market(pair = 'btcidr', amount_idr = 0, clientOrderId = '') {
        const clientIdError = this._validateClientOrderId(clientOrderId);
        if (clientIdError) return clientIdError;
        if (this.apiVersion === 'v2') {
            return this._createV2Order({
                pair, side: 'BUY', type: 'MARKET',
                quoteOrderQty: amount_idr, clientOrderId
            });
        }
        const amount = Number(amount_idr);
        if (!Number.isFinite(amount) || amount <= 0) {
            return { error: 'Market buy requires a positive IDR amount' };
        }
        const params = {
            pair: this._normalizePair(pair),
            type: 'buy',
            order_type: 'market',
            idr: String(Math.trunc(amount))
        };
        if (clientOrderId) params.client_order_id = String(clientOrderId);
        return this._requestV1('trade', params);
    }

    async sell(pair, price, amount, clientOrderId = '') {
        const clientIdError = this._validateClientOrderId(clientOrderId);
        if (clientIdError) return clientIdError;
        if (this.apiVersion === 'v2') {
            return this._createV2Order({
                pair, side: 'SELL', type: 'LIMIT', price,
                quantity: amount, clientOrderId
            });
        }
        const priceValue = this._decimalString(price);
        const amountValue = this._decimalString(amount);
        if (!priceValue || this._number(price) <= 0 ||
            !amountValue || this._number(amount) <= 0) {
            return { error: 'Limit sell requires positive price and amount' };
        }
        const params = {
            pair: this._normalizePair(pair),
            type: 'sell',
            order_type: 'limit',
            price: priceValue,
            [this._baseAsset(pair)]: amountValue
        };
        if (clientOrderId) params.client_order_id = String(clientOrderId);
        return this._requestV1('trade', params);
    }

    async sell_market(pair, amount, clientOrderId = '') {
        const clientIdError = this._validateClientOrderId(clientOrderId);
        if (clientIdError) return clientIdError;
        if (this.apiVersion === 'v2') {
            return this._createV2Order({
                pair, side: 'SELL', type: 'MARKET',
                quantity: amount, clientOrderId
            });
        }
        const amountValue = this._decimalString(amount);
        if (!amountValue || this._number(amount) <= 0) {
            return { error: 'Market sell requires a positive base-asset amount' };
        }
        const params = {
            pair: this._normalizePair(pair),
            type: 'sell',
            order_type: 'market',
            [this._baseAsset(pair)]: amountValue
        };
        if (clientOrderId) params.client_order_id = String(clientOrderId);
        return this._requestV1('trade', params);
    }

    async _createV2Order({
        pair, side, type, price, quantity, quoteOrderQty, clientOrderId
    }) {
        const symbol = this._normalizeSymbol(pair);
        const params = { symbol, side, type };
        if (type === 'LIMIT') {
            const priceValue = this._decimalString(price);
            const quantityValue = this._decimalString(quantity);
            if (!priceValue || this._number(price) <= 0 ||
                !quantityValue || this._number(quantity) <= 0) {
                return { error: 'LIMIT order requires positive price and quantity' };
            }
            params.price = priceValue;
            params.quantity = quantityValue;
            params.timeInForce = 'GTC';
        } else if (side === 'BUY') {
            if (!symbol.endsWith('IDR')) {
                return {
                    error: 'Indodax v2 documents quoteOrderQty MARKET BUY for IDR pairs only'
                };
            }
            const quoteAmount = Number(quoteOrderQty);
            if (!Number.isSafeInteger(quoteAmount) || quoteAmount <= 0) {
                return { error: 'MARKET BUY requires quoteOrderQty as a positive IDR integer' };
            }
            params.quoteOrderQty = String(quoteAmount);
        } else {
            const quantityValue = this._decimalString(quantity);
            if (!quantityValue || this._number(quantity) <= 0) {
                return { error: 'MARKET SELL requires a positive quantity' };
            }
            params.quantity = quantityValue;
        }
        if (clientOrderId) params.newClientOrderId = String(clientOrderId);
        const result = await this._requestV2('POST', '/api/v2/order', params);
        if (!result || result.error) return result;
        return this._normalizeV2Order(result, pair);
    }

    async get_order_status(pair = 'btcidr', orderId = '') {
        if (!orderId) return { error: 'order_id is required' };
        if (this.apiVersion === 'v2') {
            const result = await this._requestV2('GET', '/api/v2/order', {
                symbol: this._normalizeSymbol(pair),
                orderId: String(orderId)
            });
            if (!result || result.error) return result;
            return this._normalizeV2Order(result, pair);
        }
        const result = await this._requestV1('getOrder', {
            pair: this._normalizePair(pair),
            order_id: String(orderId)
        });
        if (!result || result.error) return result;
        return this._normalizeLegacyOrder(pair, result.order ?? result, orderId);
    }

    async get_order_by_client_id(pair = 'btcidr', clientOrderId = '') {
        if (!clientOrderId) return { error: 'client_order_id is required' };
        if (this.apiVersion === 'v2') {
            const result = await this._requestV2('GET', '/api/v2/order', {
                symbol: this._normalizeSymbol(pair),
                origClientOrderId: String(clientOrderId)
            });
            if (!result || result.error) return result;
            return this._normalizeV2Order(result, pair);
        }
        const result = await this._requestV1('getOrderByClientOrderId', {
            client_order_id: String(clientOrderId)
        });
        if (!result || result.error) return result;
        return this._normalizeLegacyOrder(pair, result.order ?? result);
    }

    async cancel_order(pair, orderId, orderType = '') {
        if (!orderId) return { error: 'order_id is required' };
        if (this.apiVersion === 'v2') {
            const result = await this._requestV2('DELETE', '/api/v2/order', {
                symbol: this._normalizeSymbol(pair),
                orderId: String(orderId)
            });
            if (!result || result.error) return result;
            return {
                success: true,
                ...this._normalizeV2Order(
                    { ...result, status: result.status || 'CANCELLED' }, pair
                )
            };
        }
        const params = {
            pair: this._normalizePair(pair),
            order_id: String(orderId)
        };
        if (orderType) params.type = String(orderType).toLowerCase();
        const result = await this._requestV1('cancelOrder', params);
        if (!result || result.error) return result;
        return { success: true, ...result };
    }

    /** V2 additionally requires pair as the optional second argument. */
    async cancel_order_by_client_id(clientOrderId, pair = '') {
        if (!clientOrderId) return { error: 'client_order_id is required' };
        if (this.apiVersion === 'v2') {
            if (!pair) {
                return {
                    error: 'pair is required to cancel an Indodax v2 order by client ID'
                };
            }
            const result = await this._requestV2('DELETE', '/api/v2/order', {
                symbol: this._normalizeSymbol(pair),
                origClientOrderId: String(clientOrderId)
            });
            if (!result || result.error) return result;
            return {
                success: true,
                ...this._normalizeV2Order(
                    { ...result, status: result.status || 'CANCELLED' }, pair
                )
            };
        }
        const result = await this._requestV1('cancelByClientOrderId', {
            client_order_id: String(clientOrderId)
        });
        if (!result || result.error) return result;
        return { success: true, ...result };
    }

    async get_ohlc(pair = 'btcidr', timeframe = '1h', limit = 100) {
        const timeframeMap = {
            '1m': '1', '5m': '5', '15m': '15', '30m': '30',
            '1h': '60', '2h': '120', '4h': '240', '6h': '360',
            '12h': '720', '1d': '1D', '3d': '3D', '1w': '1W'
        };
        const timeframeValue = timeframeMap[timeframe] || '60';
        const durationMap = {
            '1': 60, '5': 300, '15': 900, '30': 1800,
            '60': 3600, '120': 7200, '240': 14400, '360': 21600,
            '720': 43200, '1D': 86400, '3D': 259200, '1W': 604800
        };
        const now = Math.floor(Date.now() / 1000);
        const fromTime = now - ((durationMap[timeframeValue] || 3600) * limit);
        try {
            const response = await axios.get(
                'https://indodax.com/tradingview/history_v2',
                {
                    params: {
                        symbol: this._normalizeSymbol(pair),
                        tf: timeframeValue,
                        from: fromTime,
                        to: now
                    },
                    timeout: API_TIMEOUT_MS
                }
            );
            const result = response.data;
            if (!Array.isArray(result)) {
                return { error: result?.message || 'Unknown OHLC response' };
            }
            return result.map(candle => ({
                timestamp: candle.Time || 0,
                open: this._number(candle.Open),
                high: this._number(candle.High),
                low: this._number(candle.Low),
                close: this._number(candle.Close),
                volume: this._number(candle.Volume)
            }));
        } catch (error) {
            return this._formatHttpError(error, 'Failed to fetch Indodax OHLC');
        }
    }
}

module.exports = { IndodaxClient };
