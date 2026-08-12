# PLAN.md

# Multi-Account DCA Bot — Total Renovation Plan

> Dokumen ini adalah roadmap renovasi total bot DCA dari arsitektur single-account menjadi multi-account, dengan isolasi penuh API Key, state, order, posisi, konfigurasi strategi, dan log setiap akun/bot.

---

# 1. Tujuan Proyek

Mengubah bot DCA saat ini dari:

```text
Single Account
    │
    ├── config.py
    ├── dca_bot.py
    ├── dca_data.json
    └── indodax_client.py
```

Menjadi:

```text
Multi Account
│
├── Account A
│   ├── API Credentials
│   ├── Bot BTC/IDR
│   │   ├── DCA State
│   │   ├── Orders
│   │   ├── Position
│   │   └── Logs
│   │
│   └── Bot ETH/IDR
│       ├── DCA State
│       ├── Orders
│       ├── Position
│       └── Logs
│
├── Account B
│   ├── API Credentials
│   └── Bot BTC/IDR
│       ├── DCA State
│       ├── Orders
│       ├── Position
│       └── Logs
│
└── Account C
    └── ...
```

Target utama:

* Mendukung banyak akun exchange.
* Setiap akun memiliki API Key dan Secret Key sendiri.
* Setiap akun dapat menjalankan banyak bot.
* Setiap bot memiliki strategi DCA sendiri.
* State antar akun tidak boleh tercampur.
* Order dari satu akun tidak boleh mempengaruhi akun lain.
* Error satu akun tidak menghentikan akun lain.
* Dashboard dapat memonitor seluruh akun.
* Bot dapat diaktifkan/dinonaktifkan per akun.
* Bot dapat diaktifkan/dinonaktifkan per pair.
* API Key tidak disimpan plaintext.
* Tidak lagi bergantung pada satu `dca_data.json`.
* Sistem dapat melakukan recovery setelah restart.
* State bot dapat disinkronkan kembali dengan exchange.

---

# 2. Kondisi Arsitektur Saat Ini

Berdasarkan struktur project saat ini:

```text
config.py
dca_bot.py
indodax_client.py
dca_data.json
dashboard.py
dashboard.js
index.php
```

Arsitektur saat ini secara konseptual:

```text
config.py
    │
    ├── API_KEY
    └── SECRET_KEY
           │
           ▼
   IndodaxClient
           │
           ▼
      dca_bot.py
           │
           ▼
     dca_data.json
           │
           ▼
       Dashboard
```

Masalah utama:

### 2.1 Single API Credential

Bot hanya memiliki satu pasangan:

```text
API_KEY
SECRET_KEY
```

Akibatnya bot tidak memiliki konsep:

```text
Account ID
Account Name
Exchange
API Credentials
Account Status
```

---

### 2.2 Single State File

Saat ini state disimpan dalam:

```text
dca_data.json
```

Contoh state:

```json
{
    "active_position": true,
    "base_price": 1081899000,
    "base_amount_crypto": 1.3864510458000238e-05,
    "so_entries": [],
    "tp_price": 1092717990,
    "open_orders": []
}
```

State seperti ini hanya cocok untuk satu bot.

Jika digunakan untuk banyak akun:

```text
Account A → dca_data.json
Account B → dca_data.json
```

maka state akan saling menimpa.

---

### 2.3 Single Bot Lifecycle

Bot saat ini secara konsep:

```text
Start
  │
  ▼
Load Config
  │
  ▼
Connect Exchange
  │
  ▼
Load State
  │
  ▼
Run DCA
```

Arsitektur multi-account harus menjadi:

```text
Application Start
      │
      ▼
Load Accounts
      │
      ▼
For Each Account
      │
      ├── Create Exchange Client
      │
      ├── Load Account Bots
      │
      └── Start Bot Workers
              │
              ├── BTC/IDR
              ├── ETH/IDR
              └── SOL/IDR
```

---

# 3. Arsitektur Target

Arsitektur target:

```text
                    ┌──────────────────────┐
                    │      Dashboard       │
                    └──────────┬───────────┘
                               │
                               ▼
                    ┌──────────────────────┐
                    │      Bot Manager     │
                    └──────────┬───────────┘
                               │
                 ┌─────────────┼─────────────┐
                 │             │             │
                 ▼             ▼             ▼
            Account A      Account B      Account C
                 │             │             │
                 ▼             ▼             ▼
          Exchange Client Exchange Client Exchange Client
                 │             │             │
          ┌──────┴──────┐      │       ┌─────┴─────┐
          ▼             ▼      ▼       ▼           ▼
       BTC Bot       ETH Bot BTC Bot BTC Bot    ETH Bot
          │             │      │       │           │
          ▼             ▼      ▼       ▼           ▼
       State A1      State A2 State B1 State C1  State C2
```

Setiap bot harus memiliki:

```text
account_id
bot_id
exchange
pair
strategy_config
state
orders
position
logs
```

---

# 4. Prinsip Isolasi Data

Ini adalah aturan paling penting.

## 4.1 Account Isolation

Akun:

```text
account_001
account_002
account_003
```

tidak boleh berbagi:

```text
API Key
Secret Key
Balance
Order
Trade History
Position
State
```

---

## 4.2 Bot Isolation

Dalam satu akun:

```text
Account A
│
├── BTC/IDR Bot
│
├── ETH/IDR Bot
│
└── SOL/IDR Bot
```

Setiap bot harus memiliki state sendiri.

Tidak boleh:

```text
BTC Bot → membaca state ETH Bot
ETH Bot → menggunakan order BTC Bot
```

---

## 4.3 State Isolation

State ideal:

```text
Account
    │
    └── Bot
          │
          ├── Position
          ├── Base Entry
          ├── Safety Orders
          ├── Take Profit
          ├── Stop Loss
          ├── Open Orders
          └── Trade History
```

---

# 5. Struktur Folder Baru

Direkomendasikan melakukan refactor menjadi:

```text
dca-bot/
│
├── app.py
├── requirements.txt
├── .env
├── .env.example
│
├── config/
│   ├── settings.py
│   └── constants.py
│
├── core/
│   ├── bot_manager.py
│   ├── bot_worker.py
│   ├── account_manager.py
│   ├── strategy_engine.py
│   ├── state_manager.py
│   ├── order_manager.py
│   └── recovery_manager.py
│
├── exchanges/
│   ├── base_client.py
│   └── indodax_client.py
│
├── models/
│   ├── account.py
│   ├── bot.py
│   ├── order.py
│   ├── position.py
│   └── strategy.py
│
├── services/
│   ├── account_service.py
│   ├── bot_service.py
│   ├── order_service.py
│   ├── market_service.py
│   └── reconciliation_service.py
│
├── database/
│   ├── database.py
│   ├── migrations/
│   └── repositories/
│
├── api/
│   ├── accounts.py
│   ├── bots.py
│   ├── orders.py
│   └── dashboard.py
│
├── dashboard/
│   ├── index.html
│   ├── dashboard.js
│   └── dashboard.css
│
├── logs/
│
└── tests/
    ├── test_accounts.py
    ├── test_dca_strategy.py
    ├── test_orders.py
    └── test_multi_account.py
```

---

# 6. Database Migration

## PRIORITAS TINGGI

`dca_data.json` tidak boleh menjadi sumber data utama untuk multi-account.

Gunakan database.

Pilihan:

```text
SQLite
```

untuk versi lokal sederhana.

Atau:

```text
MySQL
```

untuk versi production dan multi-user.

Struktur minimal:

```text
accounts
bots
strategies
positions
orders
trades
bot_logs
system_logs
```

---

# 7. Tabel Accounts

```sql
accounts
```

Field:

```text
id
name
exchange
api_key_encrypted
api_secret_encrypted
is_active
created_at
updated_at
last_connected_at
last_error
```

Contoh:

```text
Account A
Exchange: Indodax
Status: Active

Account B
Exchange: Indodax
Status: Active

Account C
Exchange: Indodax
Status: Disabled
```

---

# 8. Tabel Bots

```sql
bots
```

Field:

```text
id
account_id
name
exchange
pair
status
dry_run
strategy_id
created_at
updated_at
```

Relasi:

```text
accounts
    │
    ├── bots
    │
    ├── bots
    │
    └── bots
```

Contoh:

```text
Account A
│
├── BTC DCA
├── ETH DCA
└── SOL DCA

Account B
│
└── BTC DCA
```

---

# 9. Tabel Strategies

Strategi harus dipisahkan dari bot.

```sql
strategies
```

Field:

```text
id
name
base_order_amount
safety_order_amount
max_safety_orders
price_deviation
deviation_scale
volume_scale
take_profit_percent
stop_loss_percent
max_position_amount
cooldown_seconds
enabled
```

Contoh:

```text
Strategy Conservative
Strategy Moderate
Strategy Aggressive
```

Bot dapat memilih strategi.

---

# 10. Tabel Positions

```sql
positions
```

Field:

```text
id
bot_id
status
base_price
average_entry_price
base_amount
total_amount
total_invested
take_profit_price
stop_loss_price
current_price
updated_at
```

Satu bot dapat memiliki satu active position:

```text
Bot ID
   │
   └── Position
```

---

# 11. Tabel Orders

```sql
orders
```

Field:

```text
id
bot_id
account_id
exchange_order_id
order_type
side
pair
price
amount
amount_quote
status
is_dca
dca_level
created_at
updated_at
```

`exchange_order_id` wajib disimpan.

Contoh:

```text
Account A
Bot BTC
Order 12345
SO #1

Account B
Bot BTC
Order 98765
SO #1
```

Walaupun sama-sama BTC, order tetap berbeda.

---

# 12. Tabel Trades

```sql
trades
```

Menyimpan transaksi yang benar-benar executed.

Field:

```text
id
account_id
bot_id
order_id
exchange_trade_id
pair
side
price
amount
fee
fee_currency
executed_at
```

Tujuannya untuk perhitungan:

```text
Total Investasi
Average Entry
Realized PnL
Unrealized PnL
Total Fees
Win Rate
```

---

# 13. Tabel Bot Logs

```sql
bot_logs
```

Field:

```text
id
account_id
bot_id
level
event
message
metadata
created_at
```

Contoh:

```text
Account A | BTC Bot | INFO
Base order executed

Account A | BTC Bot | INFO
SO #1 placed

Account B | BTC Bot | ERROR
Insufficient balance
```

---

# 14. API Credential Management

Jangan menyimpan:

```python
API_KEY = "xxx"
SECRET_KEY = "xxx"
```

di source code.

Gunakan:

```text
Environment Variables
```

untuk encryption key:

```env
ENCRYPTION_KEY=...
```

API key akun disimpan dalam database dalam bentuk terenkripsi.

Flow:

```text
User
 │
 ▼
Add Account
 │
 ▼
API Key + Secret Key
 │
 ▼
Encrypt
 │
 ▼
Database
```

Saat bot berjalan:

```text
Database
   │
   ▼
Decrypt Credential
   │
   ▼
Create Exchange Client
   │
   ▼
Run Account Bot
```

---

# 15. Exchange Client Refactor

`indodax_client.py` saat ini harus diubah agar setiap instance hanya menangani satu akun.

Target:

```python
client = IndodaxClient(
    api_key=account.api_key,
    secret_key=account.secret_key
)
```

Jangan membuat global:

```python
INDODAX_API_KEY
INDODAX_SECRET_KEY
```

sebagai sumber utama runtime.

Gunakan:

```text
Account → Credential → Client
```

Contoh:

```text
Account A
   │
   ▼
Client A
   │
   ├── get_balance()
   ├── buy()
   └── get_orders()

Account B
   │
   ▼
Client B
   │
   ├── get_balance()
   ├── buy()
   └── get_orders()
```

---

# 16. Bot Manager

Buat komponen:

```text
BotManager
```

Tugas:

1. Load semua akun aktif.
2. Load semua bot aktif.
3. Membuat client exchange.
4. Membuat worker per bot.
5. Menjalankan worker.
6. Menghentikan worker.
7. Restart worker yang error.
8. Monitoring health.

Flow:

```text
BotManager
    │
    ▼
Load Active Accounts
    │
    ▼
Load Active Bots
    │
    ▼
Create BotWorker
    │
    ▼
Start Worker
```

---

# 17. Bot Worker

Setiap bot memiliki worker sendiri.

Contoh:

```text
Worker 1
Account A / BTC

Worker 2
Account A / ETH

Worker 3
Account B / BTC
```

Worker tidak boleh berbagi mutable state.

Setiap worker memiliki:

```python
account_id
bot_id
client
strategy
state
```

---

# 18. DCA Strategy Engine

Pisahkan logic strategi dari API exchange.

Jangan:

```text
dca_bot.py
    ├── API Call
    ├── DCA Calculation
    ├── Database
    ├── Logging
    └── Dashboard
```

Pisahkan menjadi:

```text
Market Data
      │
      ▼
Strategy Engine
      │
      ▼
Trading Decision
      │
      ▼
Order Manager
      │
      ▼
Exchange Client
```

Strategy Engine hanya menentukan:

```text
BUY BASE
BUY SO
TAKE PROFIT
STOP LOSS
WAIT
```

---

# 19. Order Manager

Buat:

```text
OrderManager
```

Tugas:

* Create order.
* Track order.
* Check order status.
* Cancel order.
* Retry order.
* Validate order.
* Save exchange order ID.
* Update database.

Flow:

```text
Strategy Decision
       │
       ▼
Order Manager
       │
       ├── Validate Balance
       ├── Validate Price
       ├── Validate Amount
       ├── Submit Order
       ├── Save Order
       └── Track Status
```

---

# 20. State Management

Jangan lagi menggunakan satu:

```text
dca_data.json
```

Gunakan database sebagai source of truth.

State bot:

```text
Bot ID
    │
    ├── Position
    ├── Open Orders
    ├── DCA Entries
    ├── TP
    └── SL
```

Jika tetap membutuhkan JSON untuk backup/cache:

```text
data/
├── account_001/
│   └── bot_001.json
│
├── account_001/
│   └── bot_002.json
│
└── account_002/
    └── bot_003.json
```

Namun JSON hanya sebagai cache/backup.

Database tetap sumber utama.

---

# 21. Reconciliation System

Ini WAJIB untuk production.

Bot tidak boleh hanya percaya database.

Secara berkala:

```text
Local Database
      │
      │ Compare
      ▼
Exchange
```

Periksa:

```text
Open Orders
Balance
Order Status
Trade History
```

Contoh:

```text
Database:
Order 12345 = OPEN

Exchange:
Order 12345 = FILLED
```

Maka:

```text
Update Database
      │
      ▼
Update Position
      │
      ▼
Update DCA State
```

---

# 22. Startup Recovery

Saat bot restart:

```text
Application Start
       │
       ▼
Load Accounts
       │
       ▼
Connect Exchange
       │
       ▼
Load Database State
       │
       ▼
Fetch Open Orders
       │
       ▼
Fetch Recent Trades
       │
       ▼
Reconcile
       │
       ▼
Repair State
       │
       ▼
Start Bot
```

Bot tidak boleh langsung melakukan order setelah restart sebelum reconciliation selesai.

---

# 23. Pencegahan Duplicate Order

Tambahkan idempotency.

Setiap action harus memiliki unique key.

Contoh:

```text
account_id
bot_id
strategy_cycle
order_type
dca_level
```

Sebelum membuat order:

```text
Check Existing Order
       │
       ├── Exists → Jangan buat lagi
       │
       └── Tidak ada → Buat order
```

Tujuannya mencegah:

```text
Bot restart
    ↓
SO #1 dianggap belum dibuat
    ↓
SO #1 dibuat lagi
```

---

# 24. Concurrency

Multi-account harus menggunakan worker terisolasi.

Contoh:

```text
Account A
    ├── BTC Worker
    └── ETH Worker

Account B
    ├── BTC Worker
    └── SOL Worker
```

Gunakan:

```text
Thread
atau
AsyncIO
atau
Process
```

Untuk tahap awal, gunakan worker architecture yang sederhana dan terkontrol.

Jangan membuat satu loop besar:

```python
while True:
    for account in accounts:
        for bot in bots:
            run_bot()
```

karena satu API error dapat memblokir akun lain.

Lebih baik:

```text
BotWorker A/BTC
BotWorker A/ETH
BotWorker B/BTC
BotWorker C/SOL
```

masing-masing memiliki lifecycle sendiri.

---

# 25. Error Isolation

Jika:

```text
Account A
API Error
```

maka:

```text
Account A → ERROR
Account B → RUNNING
Account C → RUNNING
```

Jangan:

```text
Account A Error
     ↓
Main Bot Crash
     ↓
Semua Account Stop
```

Implementasikan:

```text
Worker-Level Exception Handling
```

Status worker:

```text
RUNNING
PAUSED
ERROR
DISCONNECTED
STOPPED
```

---

# 26. Rate Limit Protection

Tambahkan:

```text
Rate Limiter
Retry
Exponential Backoff
Timeout
Circuit Breaker
```

Flow:

```text
API Request
    │
    ▼
Rate Limiter
    │
    ▼
Exchange
    │
    ├── Success
    │
    └── Error
          │
          ▼
       Retry
          │
          ▼
      Backoff
```

Jangan retry order secara buta.

Untuk order trading, selalu lakukan pengecekan terlebih dahulu apakah order sebenarnya sudah diterima exchange.

---

# 27. Dashboard Multi-Account

Dashboard baru harus memiliki:

```text
Overview
Accounts
Bots
Positions
Orders
Trades
Logs
Settings
```

Overview:

```text
Total Accounts
Active Accounts
Active Bots
Open Positions
Total Invested
Realized PnL
Unrealized PnL
```

---

# 28. Account Dashboard

Contoh:

```text
Account A
────────────────────
Status: ONLINE
Balance: Rp 10.000.000

Bots:
BTC DCA    RUNNING
ETH DCA    RUNNING
SOL DCA    PAUSED
```

Account B:

```text
Account B
────────────────────
Status: ONLINE
Balance: Rp 5.000.000

Bots:
BTC DCA    RUNNING
```

---

# 29. Bot Detail Dashboard

Setiap bot menampilkan:

```text
Account
Pair
Status
Current Price
Average Entry
Base Order
DCA Count
Total Invested
Take Profit
Stop Loss
Unrealized PnL
Realized PnL
Open Orders
```

DCA visualization:

```text
BASE
  │
  ├── SO1
  ├── SO2
  ├── SO3
  ├── SO4
  └── SO5
       │
       ▼
    TAKE PROFIT
```

---

# 30. Fitur Account Management

Dashboard harus dapat:

```text
Add Account
Edit Account
Enable Account
Disable Account
Test Connection
Delete Account
```

Test Connection:

```text
API Key
    │
    ▼
Exchange API
    │
    ▼
Get Balance
    │
    ▼
Success / Failed
```

---

# 31. Fitur Bot Management

Dashboard harus dapat:

```text
Create Bot
Start Bot
Pause Bot
Stop Bot
Restart Bot
Delete Bot
Edit Strategy
```

Contoh:

```text
Account A
    │
    ├── BTC DCA
    │     ├── Start
    │     ├── Pause
    │     └── Stop
    │
    └── ETH DCA
          ├── Start
          ├── Pause
          └── Stop
```

---

# 32. Dry Run Mode

Dry Run harus tersedia di level:

```text
Global
Account
Bot
```

Contoh:

```text
Account A
BTC Bot
DRY RUN = ON

Account B
BTC Bot
DRY RUN = OFF
```

Bot dry-run tidak boleh mengirim order nyata ke exchange.

---

# 33. Security

WAJIB:

* API Secret terenkripsi.
* Jangan log API Key.
* Jangan log Secret Key.
* Jangan tampilkan Secret Key di dashboard.
* Gunakan masked credential.
* Batasi API permission.
* Jangan gunakan permission withdrawal.
* Gunakan HTTPS untuk dashboard.
* Gunakan authentication untuk dashboard.
* Tambahkan audit log.

---

# 34. Logging

Gunakan structured logging.

Format:

```text
TIMESTAMP
ACCOUNT_ID
BOT_ID
PAIR
LEVEL
EVENT
MESSAGE
```

Contoh:

```text
2026-07-22
account_001
bot_001
btcidr
INFO
DCA_ENTRY
SO #2 executed
```

Log harus dapat difilter:

```text
By Account
By Bot
By Pair
By Level
By Date
```

---

# 35. Monitoring

Tambahkan health status:

```text
Account Connection
Bot Worker
Last API Request
Last Successful Sync
Last Order
Last Error
```

Contoh:

```text
Account A
API: ONLINE
Last Sync: 10 sec ago

BTC Bot
Worker: RUNNING
Last Tick: 2 sec ago
```

---

# 36. Testing Strategy

Sebelum production, lakukan test:

## Test 1 — Single Account

```text
Account A
BTC Bot
```

Pastikan logic lama tetap berjalan.

---

## Test 2 — Two Accounts

```text
Account A → BTC
Account B → BTC
```

Pastikan:

```text
Order A ≠ Order B
State A ≠ State B
Balance A ≠ Balance B
```

---

## Test 3 — One Account Multiple Bots

```text
Account A
├── BTC
└── ETH
```

Pastikan state tidak tercampur.

---

## Test 4 — API Error

Simulasikan:

```text
Account A → API Error
```

Pastikan:

```text
Account A → ERROR
Account B → RUNNING
```

---

## Test 5 — Restart

```text
Bot Running
    ↓
Restart
    ↓
Recovery
    ↓
Reconciliation
    ↓
Resume
```

Pastikan tidak terjadi duplicate order.

---

## Test 6 — Network Failure

Simulasikan:

```text
Internet Disconnect
```

Pastikan bot:

```text
Retry
Backoff
Recover
```

---

## Test 7 — Duplicate Protection

Simulasikan worker restart saat order sedang diproses.

Pastikan:

```text
Tidak ada duplicate order
```

---

# 37. Migrasi Data Lama

Data saat ini:

```text
dca_data.json
```

harus dimigrasikan.

Flow:

```text
dca_data.json
      │
      ▼
Migration Script
      │
      ├── Create Account
      │
      ├── Create Bot
      │
      ├── Create Position
      │
      ├── Import Orders
      │
      └── Import Trades
```

Contoh:

```text
Account Legacy
ID: account_legacy

Bot Legacy
ID: bot_legacy
Pair: btcidr
```

Setelah migrasi:

```text
accounts
    │
    └── account_legacy
            │
            └── bot_legacy
                    │
                    ├── position
                    ├── orders
                    └── trades
```

---

# 38. Urutan Implementasi

## PHASE 1 — Audit

* [ ] Audit `dca_bot.py`
* [ ] Audit `indodax_client.py`
* [ ] Audit `config.py`
* [ ] Audit `dca_data.json`
* [ ] Audit `dashboard.py`
* [ ] Audit `dashboard.js`
* [ ] Dokumentasikan seluruh flow trading
* [ ] Identifikasi semua global variable
* [ ] Identifikasi seluruh state variable
* [ ] Identifikasi semua API call
* [ ] Identifikasi semua bug logic trading

---

## PHASE 2 — Refactor Exchange Client

* [ ] Buat `BaseExchangeClient`
* [ ] Refactor `IndodaxClient`
* [ ] Hilangkan dependency global API Key
* [ ] Client harus menerima credentials melalui constructor
* [ ] Tambahkan timeout
* [ ] Tambahkan retry
* [ ] Tambahkan error normalization
* [ ] Tambahkan rate-limit handling

---

## PHASE 3 — Database

* [ ] Setup database
* [ ] Buat tabel accounts
* [ ] Buat tabel bots
* [ ] Buat tabel strategies
* [ ] Buat tabel positions
* [ ] Buat tabel orders
* [ ] Buat tabel trades
* [ ] Buat tabel logs
* [ ] Buat migration script

---

## PHASE 4 — Account Manager

* [ ] Create account
* [ ] Update account
* [ ] Delete account
* [ ] Enable account
* [ ] Disable account
* [ ] Test connection
* [ ] Encrypt credentials

---

## PHASE 5 — Bot Manager

* [ ] Create BotManager
* [ ] Create BotWorker
* [ ] Account isolation
* [ ] Bot isolation
* [ ] Worker lifecycle
* [ ] Worker error isolation
* [ ] Worker restart

---

## PHASE 6 — Strategy Engine

* [ ] Pisahkan DCA calculation
* [ ] Pisahkan TP calculation
* [ ] Pisahkan SL calculation
* [ ] Pisahkan SO calculation
* [ ] Pisahkan average entry calculation
* [ ] Tambahkan strategy configuration
* [ ] Tambahkan validation

---

## PHASE 7 — Order Manager

* [ ] Create order
* [ ] Track order
* [ ] Check order
* [ ] Cancel order
* [ ] Retry order
* [ ] Idempotency
* [ ] Duplicate protection

---

## PHASE 8 — State & Recovery

* [ ] Database state
* [ ] Reconciliation
* [ ] Startup recovery
* [ ] Open order synchronization
* [ ] Trade synchronization
* [ ] Position recovery

---

## PHASE 9 — Dashboard

* [ ] Account list
* [ ] Account status
* [ ] Bot list
* [ ] Bot status
* [ ] Position dashboard
* [ ] Order dashboard
* [ ] Trade history
* [ ] PnL dashboard
* [ ] Logs
* [ ] Account management
* [ ] Bot management

---

## PHASE 10 — Testing

* [ ] Single account
* [ ] Multi-account
* [ ] Multi-bot
* [ ] API failure
* [ ] Network failure
* [ ] Restart recovery
* [ ] Duplicate order
* [ ] Balance insufficient
* [ ] Order rejected
* [ ] Partial execution

---

# 39. Recommended Final Architecture

```text
                         DASHBOARD
                             │
                             ▼
                       API / CONTROLLER
                             │
                             ▼
                        BOT MANAGER
                             │
            ┌────────────────┼────────────────┐
            │                │                │
            ▼                ▼                ▼
        ACCOUNT A        ACCOUNT B        ACCOUNT C
            │                │                │
            ▼                ▼                ▼
       CLIENT A          CLIENT B          CLIENT C
            │                │                │
       ┌────┴────┐       ┌───┴────┐       ┌───┴────┐
       ▼         ▼       ▼        ▼       ▼        ▼
     BTC       ETH      BTC      ETH     BTC      SOL
     BOT       BOT      BOT      BOT     BOT      BOT
       │         │       │        │       │        │
       ▼         ▼       ▼        ▼       ▼        ▼
   STRATEGY  STRATEGY STRATEGY STRATEGY STRATEGY STRATEGY
       │         │       │        │       │        │
       └─────────┴───────┴────────┴───────┴────────┘
                             │
                             ▼
                       ORDER MANAGER
                             │
                             ▼
                        EXCHANGE API

                             │
                             ▼
                         DATABASE
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
       Accounts           Bots              Orders
       Positions          Trades            Logs
```

---

# 40. Critical Rules

Selama renovasi, aturan berikut WAJIB dipatuhi:

### Rule 1

Jangan mengubah logic strategi DCA sebelum logic lama sudah dipetakan.

### Rule 2

Jangan menghapus `dca_data.json` sebelum migration berhasil.

### Rule 3

Jangan menjalankan multi-account menggunakan global API credentials.

### Rule 4

Jangan menggunakan satu state untuk banyak bot.

### Rule 5

Jangan menganggap database selalu sama dengan exchange.

### Rule 6

Setiap order harus memiliki:

```text
account_id
bot_id
exchange_order_id
```

### Rule 7

Setiap bot harus dapat dijalankan secara independen.

### Rule 8

Error satu account tidak boleh menghentikan account lain.

### Rule 9

Restart bot tidak boleh menghasilkan duplicate order.

### Rule 10

Bot tidak boleh melakukan trading sebelum recovery dan reconciliation selesai.

---

# 41. Definition of Done

Renovasi dianggap selesai apabila:

```text
[✓] 2+ akun dapat aktif bersamaan
[✓] Setiap akun memiliki API Key sendiri
[✓] API credential terenkripsi
[✓] 1 akun dapat menjalankan banyak bot
[✓] Setiap bot memiliki state sendiri
[✓] Order terisolasi berdasarkan account_id + bot_id
[✓] Tidak ada global API credential
[✓] Tidak ada single dca_data.json sebagai state utama
[✓] Database menjadi source of truth
[✓] Startup recovery tersedia
[✓] Reconciliation tersedia
[✓] Duplicate order protection tersedia
[✓] Error account terisolasi
[✓] Dashboard multi-account tersedia
[✓] Dry-run tersedia per bot
[✓] Logging dapat difilter per account/bot
[✓] Bot dapat restart tanpa merusak posisi
[✓] Semua test multi-account berhasil
```

---

# 42. Urutan Pengerjaan Paling Aman

Urutan implementasi yang disarankan:

```text
1. Audit Existing Bot
        ↓
2. Backup Existing Bot
        ↓
3. Freeze Existing Trading Logic
        ↓
4. Refactor IndodaxClient
        ↓
5. Introduce Account Model
        ↓
6. Introduce Bot Model
        ↓
7. Introduce Database
        ↓
8. Migrate dca_data.json
        ↓
9. Build State Manager
        ↓
10. Build Order Manager
        ↓
11. Build Bot Worker
        ↓
12. Build Bot Manager
        ↓
13. Build Reconciliation
        ↓
14. Build Recovery
        ↓
15. Test Single Account
        ↓
16. Test Multi Account
        ↓
17. Test Multi Bot
        ↓
18. Build Multi Account Dashboard
        ↓
19. Dry Run
        ↓
20. Production Deployment
```

---

# 43. Target Akhir

Sistem final harus mampu menjalankan skenario:

```text
ACCOUNT A
Indodax
│
├── BTC/IDR DCA
│   ├── Base Order
│   ├── SO1
│   ├── SO2
│   └── TP
│
└── ETH/IDR DCA
    ├── Base Order
    ├── SO1
    └── TP


ACCOUNT B
Indodax
│
├── BTC/IDR DCA
│   ├── Base Order
│   ├── SO1
│   ├── SO2
│   └── TP
│
└── SOL/IDR DCA
    ├── Base Order
    └── TP
```

Semua berjalan bersamaan:

```text
Account A / BTC  → RUNNING
Account A / ETH  → RUNNING
Account B / BTC  → RUNNING
Account B / SOL  → PAUSED
```

Jika Account A mengalami error:

```text
Account A / BTC → ERROR
Account A / ETH → ERROR
Account B / BTC → RUNNING
Account B / SOL → PAUSED
```

Jika hanya BTC Account A yang bermasalah:

```text
Account A / BTC → ERROR
Account A / ETH → RUNNING
Account B / BTC → RUNNING
```

**Target utama renovasi adalah membuat setiap kombinasi `Account + Bot + Pair` menjadi unit trading yang independen, terisolasi, dapat dipulihkan, dan dapat dimonitor.**
