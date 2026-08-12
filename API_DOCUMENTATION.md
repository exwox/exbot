# 📚 Indodax API Client Documentation

> Catatan: contoh `indodax_client.py` dan `config.py` di dokumen ini adalah referensi legacy. Runtime production menggunakan `exchanges/indodax_client.py`; credential dimasukkan melalui Settings dan disimpan terenkripsi di SQLite, bukan di `config.py`.

Dokumentasi lengkap untuk `indodax_client.py` yang telah diupdate sesuai dengan [dokumentasi resmi Indodax](https://github.com/btcid/indodax-official-api-docs).

## 🔄 Perubahan dari Versi Sebelumnya

### ✅ **Endpoint yang Diperbaiki**

1. **OHLCV Endpoint** - Sekarang menggunakan endpoint resmi `/tradingview/history_v2`
2. **Trade History** - Menambahkan dukungan untuk Trade API 2.0 (`/api/v2/myTrades`)
3. **Order History** - Menambahkan dukungan untuk Trade API 2.0 (`/api/v2/order/histories`)

### 🆕 **Endpoint Baru yang Ditambahkan**

#### Private API (Memerlukan API Key)
- `get_trade_history_v2()` - Trade history dengan Trade API 2.0
- `get_order_history_v2()` - Order history dengan Trade API 2.0
- `cancel_order()` - Membatalkan order
- `get_open_orders()` - Melihat order yang masih terbuka
- `get_order_status()` - Mengambil status dan kuantitas fill kumulatif
- `get_order_by_client_id()` - Memulihkan order setelah ACK submit hilang
- `cancel_order_by_client_id()` - Membatalkan durable intent tanpa exchange order ID

#### Public API (Tanpa API Key)
- `get_server_time()` - Waktu server Indodax
- `get_pairs()` - Daftar pair yang tersedia
- `get_ticker_all()` - Ticker untuk semua pair
- `get_price_increments()` - Price increments
- `get_summaries()` - Summary market
- `get_public_trades()` - Riwayat transaksi publik

### Normalisasi status order

Runtime `exchanges/indodax_client.py` mempertahankan field mentah dan menambahkan
`amount`, `amount_remaining`, `filled_amount`, `filled_quote`, serta `status`.
Order sell dihitung dari `order_<coin>/remain_<coin>`, sedangkan order buy dari
`order_idr/remain_idr` lalu dikonversi dengan harga order. Bentuk ini mengikuti
[Private REST API resmi Indodax](https://github.com/btcid/indodax-official-api-docs/blob/master/Private-RestAPI.md#open-orders-endpoints).

### Idempotensi dan recovery order

Runtime Python menerima `client_order_id` pada limit/market buy dan sell. Worker
membuat record intent di SQLite sebelum request private API dikirim. Apabila
request timeout setelah order sebenarnya diterima exchange, worker memanggil
`get_order_by_client_id()` dan melanjutkan order yang sama. ID dibatasi maksimal
36 karakter dan tidak digunakan ulang untuk order berbeda.

Lifecycle lokal membedakan `REQUESTED`, `SUBMISSION_UNKNOWN`, `OPEN`,
`PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, dan `FAILED`. Posisi base memakai
`PENDING_BASE` sampai kuantitas fill exchange diketahui. Stop-loss market tetap
terbuka di SQLite sampai fill final; submit yang sukses bukan bukti posisi telah
tertutup.

---

## 📖 Cara Penggunaan

### 1. Inisialisasi Client

```python
from indodax_client import IndodaxClient
from config import INDODAX_API_KEY, INDODAX_SECRET_KEY

# Inisialisasi client
client = IndodaxClient(INDODAX_API_KEY, INDODAX_SECRET_KEY)
```

### 2. Public API (Tanpa Authentication)

#### Mendapatkan Waktu Server
```python
server_time = client.get_server_time()
print(f"Server time: {server_time['server_time']}")
```

#### Mendapatkan Daftar Pair
```python
pairs = client.get_pairs()
for pair in pairs:
    print(f"{pair['id']} - {pair['description']}")
```

#### Mendapatkan Ticker Semua Pair
```python
tickers = client.get_ticker_all()
if 'tickers' in tickers:
    for pair_id, ticker in tickers['tickers'].items():
        print(f"{pair_id}: Rp {ticker['last']:,.0f}")
```

#### Mendapatkan Ticker Spesifik
```python
ticker = client.get_ticker('btcidr')
if 'ticker' in ticker:
    print(f"BTC Price: Rp {ticker['ticker']['last']:,.0f}")
```

#### Mendapatkan Order Book
```python
orderbook = client.get_orderbook('btcidr')
if 'buy' in orderbook and 'sell' in orderbook:
    best_bid = orderbook['buy'][0]
    best_ask = orderbook['sell'][0]
    print(f"Bid: Rp {best_bid[0]:,.0f}, Ask: Rp {best_ask[0]:,.0f}")
```

#### Mendapatkan Data OHLCV
```python
# Timeframe yang didukung: 1m, 5m, 15m, 30m, 1h, 2h, 4h, 6h, 12h, 1d, 3d, 1w
candles = client.get_ohlc('btcidr', timeframe='1h', limit=100)
for candle in candles:
    print(f"Time: {candle['timestamp']}, Close: Rp {candle['close']:,.0f}")
```

#### Mendapatkan Summary Market
```python
summaries = client.get_summaries()
if 'tickers' in summaries:
    btc_summary = summaries['tickers'].get('btc_idr')
    if btc_summary:
        print(f"BTC 24h high: Rp {btc_summary['high']:,.0f}")
        print(f"BTC 24h low: Rp {btc_summary['low']:,.0f}")
```

### 3. Private API (Memerlukan Authentication)

#### Mendapatkan Saldo Akun
```python
balance = client.get_balance()
if 'balance' in balance:
    idr_balance = balance['balance'].get('idr', 0)
    btc_balance = balance['balance'].get('btc', 0)
    print(f"IDR: Rp {idr_balance:,.0f}")
    print(f"BTC: {btc_balance:.8f}")
```

#### Melakukan Pembelian
```python
# Limit order dengan durable client ID
result = client.buy(
    'btcidr', price=700000000, amount=0.001,
    client_order_id='xb_example_base_001'
)
if 'order_id' in result:
    print(f"Order placed! ID: {result['order_id']}")

# Market order (menggunakan buy_market)
result = client.buy_market(
    'btcidr', amount_idr=100000,
    client_order_id='xb_example_market_001'
)
if 'order_id' in result:
    print(f"Market order placed! ID: {result['order_id']}")
```

#### Mendapatkan Status Order
```python
status = client.get_order_status('btcidr', order_id='12345')
if 'status' in status:
    print(f"Order status: {status['status']}")

# Recovery jika response submit timeout/hilang
status = client.get_order_by_client_id(
    'btcidr', client_order_id='xb_example_base_001'
)
```

#### Mendapatkan Trade History (Legacy)
```python
# Menggunakan endpoint lama (masih berfungsi)
trades = client.get_trade_history('btcidr', limit=50)
for trade in trades:
    print(f"Price: Rp {trade['price']:,.0f}, Amount: {trade['amount']}")
```

#### Mendapatkan Trade History (Trade API 2.0) - **RECOMMENDED**
```python
# Menggunakan endpoint baru (lebih stabil)
import time

# Get trades from last 24 hours
end_time = int(time.time() * 1000)
start_time = end_time - (24 * 60 * 60 * 1000)  # 24 hours ago

trades = client.get_trade_history_v2(
    pair='btcidr',
    limit=100,
    start_time=start_time,
    end_time=end_time
)

for trade in trades:
    print(f"Trade ID: {trade['tradeId']}")
    print(f"Price: Rp {trade['price']:,.0f}")
    print(f"Amount: {trade['qty']}")
    print(f"Fee: Rp {trade['commission']:,.0f}")
```

#### Mendapatkan Order History (Trade API 2.0) - **RECOMMENDED**
```python
orders = client.get_order_history_v2(
    pair='btcidr',
    limit=50,
    sort='desc'  # or 'asc'
)

for order in orders:
    print(f"Order ID: {order['orderId']}")
    print(f"Side: {order['side']}")
    print(f"Type: {order['type']}")
    print(f"Status: {order['status']}")
    print(f"Price: Rp {order['price']:,.0f}")
    print(f"Amount: {order['oriQty']}")
```

#### Mendapatkan Open Orders
```python
open_orders = client.get_open_orders('btcidr')
for order in open_orders:
    print(f"Order ID: {order['order_id']}")
    print(f"Type: {order['type']}, Price: Rp {order['price']:,.0f}")
```

#### Membatalkan Order
```python
result = client.cancel_order('btcidr', order_id='12345', order_type='buy')
if result.get('success'):
    print("Order cancelled successfully!")

# Jika exchange order ID belum tersimpan
result = client.cancel_order_by_client_id('xb_example_base_001')
```

---

## ⚠️ **PENTING: Migrasi ke Trade API 2.0**

### **Deadline Migrasi: 7 April 2026**

Endpoint lama (`/tapi`) untuk trade history dan order history akan **dihapus** pada tanggal tersebut. Pastikan untuk bermigrasi ke endpoint baru.

### **Perbedaan Utama:**

| Aspek | Legacy (`/tapi`) | Trade API 2.0 |
|-------|------------------|---------------|
| **Endpoint** | `/tapi` | `/api/v2/myTrades`, `/api/v2/order/histories` |
| **Method** | POST | GET |
| **Authentication** | `Key` header | `X-APIKEY` header |
| **Parameters** | `from_id`, `end_id` | `startTime`, `endTime` |
| **Max Limit** | 1000 | 1000 |
| **Default Limit** | - | 500 (trades), 100 (orders) |

### **Contoh Migrasi:**

**Sebelum (Legacy):**
```python
trades = client.get_trade_history('btcidr', limit=100)
```

**Sesudah (Trade API 2.0):**
```python
import time

# Get last 24 hours
end_time = int(time.time() * 1000)
start_time = end_time - (24 * 60 * 60 * 1000)

trades = client.get_trade_history_v2(
    pair='btcidr',
    limit=100,
    start_time=start_time,
    end_time=end_time
)
```

---

## 🔧 **Error Handling**

Semua method mengembalikan dictionary dengan salah satu format:

### **Success Response:**
```python
{
    'data': [...],  # or specific data structure
    # or
    'return': {...},
    # or
    'ticker': {...}
}
```

### **Error Response:**
```python
{
    'error': 'Error message',
    'code': 1109  # (optional, for Trade API 2.0)
}
```

### **Contoh Error Handling:**
```python
result = client.get_balance()

if 'error' in result:
    print(f"Error: {result['error']}")
    if 'code' in result:
        print(f"Error code: {result['code']}")
else:
    print(f"Balance: {result}")
```

### **Error Codes (Trade API 2.0):**

| Code | Description |
|------|-------------|
| 1000 | Internal server error |
| 1001 | Invalid credentials |
| 1002 | Invalid timestamp |
| 1003 | Invalid nonce |
| 1101 | Sign not found |
| 1102 | API key not found |
| 1103 | Trade API disabled |
| 1106 | No permission |
| 1109 | Invalid parameter |
| 1110 | Invalid symbol |
| 1112 | Order not found |

---

## 📊 **Rate Limits**

### **Public API:**
- **180 requests per minute**

### **Private API:**
- Tidak ada rate limit yang spesifik, tetapi disarankan untuk tidak melakukan request berlebihan

---

## 🌐 **Base URLs**

### **Public API:**
- `https://indodax.com/api`

### **Private API (Legacy):**
- `https://indodax.com/tapi`

### **Trade API 2.0:**
- Primary: `https://tapi.indodax.com`
- Alternative: `https://tapi.btcapi.net`

---

## 🔒 **Keamanan**

1. **JANGAN PERNAH** share API Key dan Secret Key
2. Gunakan API Key dengan permission yang sesuai:
   - **View** - Hanya untuk membaca data
   - **Trade** - Untuk melakukan trading
   - **Withdraw** - Untuk penarikan (hati-hati!)
3. Simpan `.env` dan backup database dengan aman; jangan simpan credential di `config.py`
4. Gunakan mode Dry Run untuk testing

---

## 📝 **Contoh Lengkap: DCA Bot dengan Trade API 2.0**

```python
from indodax_client import IndodaxClient
from config import INDODAX_API_KEY, INDODAX_SECRET_KEY
import time

# Inisialisasi
client = IndodaxClient(INDODAX_API_KEY, INDODAX_SECRET_KEY)

# Cek saldo
balance = client.get_balance()
if 'error' in balance:
    print(f"Error: {balance['error']}")
    exit()

idr_balance = float(balance['balance'].get('idr', 0))
print(f"IDR Balance: Rp {idr_balance:,.0f}")

# Dapatkan harga saat ini
ticker = client.get_ticker('btcidr')
if 'ticker' in ticker:
    current_price = float(ticker['ticker']['last'])
    print(f"BTC Price: Rp {current_price:,.0f}")

# Dapatkan trade history dengan Trade API 2.0
end_time = int(time.time() * 1000)
start_time = end_time - (7 * 24 * 60 * 60 * 1000)  # Last 7 days

trades = client.get_trade_history_v2(
    pair='btcidr',
    limit=100,
    start_time=start_time,
    end_time=end_time
)

if 'error' not in trades:
    total_btc = sum(float(trade['qty']) for trade in trades)
    total_idr = sum(float(trade['quoteQty']) for trade in trades)
    print(f"Total BTC traded: {total_btc:.8f}")
    print(f"Total IDR traded: Rp {total_idr:,.0f}")

# Lakukan pembelian jika saldo cukup
if idr_balance >= 100000:  # Minimum 100k
    print("Executing DCA purchase...")
    result = client.buy_market('btcidr', amount_idr=100000)
    
    if 'error' in result:
        print(f"Purchase failed: {result['error']}")
    else:
        print(f"Purchase successful! Order ID: {result.get('order_id')}")
```

---

## 🆘 **Troubleshooting**

### **Error: "Invalid credentials"**
- API Key atau Secret Key salah
- API Key belum aktif atau sudah expired
- Solusi: Generate API Key baru di Indodax

### **Error: "No permission"**
- API Key tidak memiliki permission yang cukup
- Solusi: Buat API Key dengan permission yang sesuai (Trade untuk trading)

### **Error: "Invalid timestamp"**
- Waktu sistem tidak sinkron dengan server Indodax
- Solusi: Sinkronkan waktu sistem atau gunakan `get_server_time()`

### **OHLCV mengembalikan error**
- Endpoint lama sudah tidak didukung
- Solusi: Update ke versi terbaru yang menggunakan `/tradingview/history_v2`

---

## 📞 **Support**

Untuk pertanyaan atau masalah:
1. Cek [dokumentasi resmi Indodax](https://github.com/btcid/indodax-official-api-docs)
2. Hubungi support Indodax
3. Cek log file untuk detail error

---

**Last Updated:** June 15, 2026  
**API Version:** 2.0.1  
**Client Version:** 2.0.0
