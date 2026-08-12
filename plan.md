# XBot Security and Reliability Improvement Plan

Dokumen ini adalah roadmap perbaikan XBot berdasarkan audit arsitektur, autentikasi, penyimpanan kredensial, deployment Docker, dan test suite. Target utama adalah memastikan bot aman dan stabil sebelum digunakan dalam mode live.

## Status Eksekusi — 10 Agustus 2026

Implementasi lokal yang sudah selesai:

- [x] Backup database pra-perubahan dibuat dan lolos `PRAGMA quick_check`.
- [x] Credential baru memakai AES-256-GCM v2 dengan salt/nonce acak dan kompatibilitas baca CBC/Fernet.
- [x] Migrasi credential transaksional, idempotent, dan mode dry-run tersedia.
- [x] Dashboard `/` dan semua halaman private memerlukan autentikasi serta ownership user.
- [x] Session dipindahkan dari memory/localStorage ke SQLite dan cookie HttpOnly.
- [x] Password baru memakai scrypt; hash PBKDF2 lama dinaikkan saat login.
- [x] Login/register dan mutation API memiliki rate limit; cross-site mutation ditolak.
- [x] Role admin eksplisit dan bootstrap tidak menggunakan password default.
- [x] Entry point resmi dikonsolidasikan menjadi Node dashboard + Python Bot Manager.
- [x] Dry-run menjadi default, strategy tervalidasi, dan modal siklus diperiksa sebelum live entry.
- [x] Base fill disimpan sebelum TP/SO untuk mengurangi risiko pembelian ganda setelah restart.
- [x] BO/SO/TP/SL memakai durable order intent dan `client_order_id` unik yang disimpan sebelum request exchange; ACK hilang dapat dipulihkan tanpa submit order baru.
- [x] Base order memakai status posisi `PENDING_BASE`; fill penuh maupun parsial direkonsiliasi sebelum TP/SO dibuat.
- [x] Fill kumulatif SO/TP direkonsiliasi sebagai delta idempoten; replay respons exchange tidak menggandakan inventory, fee, atau trade ledger.
- [x] Market stop-loss tidak lagi dianggap selesai saat submit; partial/final fill dipantau dan ledger direkam sebagai delta idempoten.
- [x] Fill parsial yang berakhir `CANCELLED` pada base/TP dipertahankan saat restart; sisa inventory dipromosikan atau dilindungi ulang tanpa replay trade.
- [x] Startup posisi aktif memulihkan intent TP/SO yang hilang dari JSON dan menerapkan fill downtime sebelum order strategi dibatalkan atau dibangun ulang.
- [x] Normalisasi order mengikuti field resmi Indodax untuk sell (`order_<coin>/remain_<coin>`) dan buy (`order_idr/remain_idr`).
- [x] Stop/cancel hanya menyentuh exchange order ID yang tersimpan untuk bot terkait, bukan seluruh order pada pair yang sama.
- [x] Stop/reset bersifat fail-closed: reset ditolak ketika bot masih berjalan dan ID order yang gagal dibatalkan dipertahankan untuk retry/recovery serta memicu alert kritis.
- [x] Liveness dan readiness tersedia; readiness memeriksa SQLite serta heartbeat Python Bot Manager.
- [x] Circuit breaker menahan tick setelah error API berulang, langsung trip pada nonce/timestamp drift, dan memberi cooldown sebelum guarded retry.
- [x] Exposure live per akun mencadangkan modal penuh setiap siklus; batas opsional menolak entry sebelum submit exchange.
- [x] Redaksi log terpusat melindungi credential/session/signature pada Node dan Python; worker menyertakan correlation `tick_id`, `cycle_id`, dan `client_order_id`.
- [x] Backup SQLite online terenkripsi menyediakan checksum, quick-check, retensi, verifikasi, dan restore aman ke target baru.
- [x] Snapshot aktual `xbot-20260810T150215Z-ed05065a.xbk` dibuat dari database aktif dan lulus checksum, decrypt, restore, serta SQLite quick-check (1 posisi, 1 siklus, 6 order aktif berhasil dipulihkan pada drill sementara).
- [x] Alert operasional persisten dan terdeduplikasi tersedia untuk restart loop, kegagalan dekripsi, circuit exchange, dan mismatch rekonsiliasi; API baca/acknowledge dibatasi ownership tenant.
- [x] Log file Python dan stdout container Node/Python memiliki batas ukuran serta retensi.
- [x] Live trading memakai gate fail-closed lintas Node/Python: flag operator, frasa konfirmasi risiko, dan exposure cap nonzero wajib tersedia sebelum `dry_run=false` dapat dibuat atau dijalankan.
- [x] Rollout live dibatasi allowlist bot eksplisit dan bukti minimal tiga siklus dry-run `CLOSED`; readiness bot tetap tenant-scoped.
- [x] Dependency native `sqlite3` dikompilasi di dalam image dan diuji saat build agar binary host/GLIBC yang tidak kompatibel tidak memicu restart loop.
- [x] Ledger dry-run menyimpan BO/SO/TP dengan status lifecycle yang konsisten dan memperbaiki otomatis pseudo-order legacy saat restart.
- [x] Test Node, Python, encryption bridge, migration, auth restart, tenant isolation, dan CSRF lulus.
- [x] CI, `.dockerignore`, deployment guide, rollback, dan recovery runbook tersedia.
- [x] `npm audit` melaporkan 0 vulnerability setelah lockfile diperbarui.
- [x] Image `xbot:1.0.0` berhasil dibangun dan container tervalidasi sehat melalui `/healthz` serta `/readyz`.
- [x] Bootstrap administrator berhasil; `ADMIN_PASSWORD` kemudian dikosongkan dan runtime direcreate tanpa kehilangan posisi dry-run.
- [x] Versi fail-closed Stop/Reset dideploy pada 10 Agustus 2026 22:04 WIB; startup reconciliation mempertahankan 1 posisi, 1 siklus, dan 6 exchange ID pseudo-order yang unik tanpa alert terbuka.
- [x] Operator menyetujui profil risiko pilot: stop-loss 8%, modal posisi maksimum Rp90.000, exposure akun Rp100.000, dan anggaran rugi operasional Rp10.000 per siklus; gate live tetap ditutup sampai rollout terpenuhi.
- [x] Readiness hanya menghitung dry-run yang ditutup TP/SL dengan exit nyata; reset manual tidak dihitung. Gate Node/Python juga menolak stop-loss nol dan batas posisi/exposure di bawah modal siklus.
- [x] Tool `scripts/set_bot_risk.py` menyediakan preview/apply fail-closed dan hanya mengubah strategi bot dry-run jika batas posisi menutup seluruh modal siklus.

Belum dapat/layak diselesaikan otomatis di workspace ini:

- [ ] Partial fill dan recovery crash BO/SO/TP/SL sudah lulus pada mocked exchange; satu siklus pada `demo-indodax.com` tetap harus divalidasi operator sebelum live.
- [ ] HTTPS reverse proxy, firewall, alerting eksternal, backup off-host, dan live rollout memerlukan akses VPS/operator.
- [ ] Live trading tetap dilarang sampai checklist Fase 8 dijalankan oleh operator.

Validasi terakhir pada 10 Agustus 2026:

- Python: 49 test lulus.
- Node unit: 7 test lulus.
- Integration: 1 test lulus (auth, tenant isolation, restart session, liveness/readiness, serta alur Stop → Reset fail-closed).
- `npm audit`: 0 vulnerability.
- Pemeriksaan sintaks Python/Node dan `docker compose config --quiet`: lulus.
- Build image `xbot:1.0.0`, startup container, `/healthz`, dan `/readyz`: lulus melalui eksekusi operator.
- Restart saat satu posisi dry-run terbuka: lulus; rekonsiliasi menghasilkan tepat satu posisi, satu siklus, lima SO aktif, dan satu TP aktif.

## Tujuan

- Menghilangkan kegagalan startup akibat format encryption key yang tidak konsisten.
- Mencegah akses tanpa izin dan kebocoran data lintas pengguna.
- Melindungi API key Indodax dari pembacaan dan manipulasi ciphertext.
- Memperkuat autentikasi dan pengelolaan session.
- Menyatukan entry point serta konfigurasi Node dan Python.
- Menyediakan test suite dan proses deployment yang dapat dipercaya.

## Prinsip Pelaksanaan

- Semua pengembangan dan pengujian awal wajib menggunakan `dry_run=true`.
- Backup database dan `.env` sebelum melakukan migrasi enkripsi atau schema.
- Jangan menghapus implementasi legacy sebelum data dan alur produksinya teridentifikasi.
- Setiap fase harus lulus kriteria selesai sebelum masuk ke fase berikutnya.
- Aktivasi live trading dilakukan terakhir melalui rollout bertahap.

## Fase 0 — Persiapan dan Baseline

Prioritas: P0

### Pekerjaan

- [ ] Dokumentasikan command resmi untuk development, test, setup database, dan production.
- [ ] Catat versi Node.js, Python, SQLite, dan dependency yang digunakan Docker.
- [ ] Buat backup terenkripsi untuk `data/dca_bot.db` dan simpan salinan `.env` di lokasi aman.
- [ ] Identifikasi akun dan bot yang sedang berstatus `RUNNING`.
- [ ] Pastikan semua bot berada dalam mode dry-run selama proses perbaikan.
- [ ] Tambahkan checklist rollback untuk perubahan database dan enkripsi.

### Kriteria selesai

- Backup dapat dipulihkan ke database sementara.
- Tidak ada order live baru selama proses migrasi.
- Tim memiliki satu command startup production yang terdokumentasi.

## Fase 1 — Perbaikan Kritis

Prioritas: P0, wajib selesai sebelum live trading.

### 1.1 Satukan format `ENCRYPTION_KEY`

Masalah: dokumentasi menghasilkan key hex, sedangkan Python mengharapkan key Fernet. Konfigurasi tersebut dapat membuat Python Bot Manager gagal startup dan menyebabkan container restart berulang.

### Pekerjaan

- [ ] Tentukan satu format key utama untuk Node dan Python.
- [ ] Rekomendasi: terima secret acak sebagai master key, lalu derivasi key AES-GCM secara identik di kedua runtime.
- [ ] Tambahkan validasi key saat startup Node dan Python.
- [ ] Hentikan startup dengan pesan yang jelas apabila key kosong atau tidak valid.
- [ ] Perbarui `.env.example`, README, `setup-db.js`, dan pesan bantuan pada Python.
- [ ] Tambahkan integration test yang mengenkripsi di Node lalu mendekripsi di Python, serta arah sebaliknya.
- [ ] Pertahankan pembacaan format legacy selama masa migrasi.

### Kriteria selesai

- Key yang dibuat mengikuti dokumentasi dapat menjalankan seluruh container.
- Node dan Python dapat membaca ciphertext satu sama lain.
- Invalid key menghasilkan kegagalan startup yang konsisten, bukan error saat bot sudah berjalan.

### 1.2 Lindungi halaman utama

Masalah: route `/` tidak memeriksa user dan menggunakan akun aktif global pertama untuk mengambil balance.

### Pekerjaan

- [ ] Redirect request tanpa session dari `/` ke `/login`.
- [ ] Ganti `getActiveAccounts()` dengan query akun milik `req.user.id`.
- [ ] Pastikan semua data dashboard berasal dari bot/account milik user aktif.
- [ ] Jangan melakukan request private Indodax sebelum autentikasi dan ownership berhasil.
- [ ] Audit seluruh route HTML dan API dengan matriks autentikasi serta ownership.
- [ ] Tambahkan test untuk akses anonymous dan akses lintas-user.

### Kriteria selesai

- Pengunjung tanpa login tidak dapat memicu request private Indodax.
- User A tidak dapat membaca balance, bot, strategy, order, trade, atau log User B.
- ID account atau bot yang ditebak menghasilkan `404` atau `403` tanpa membocorkan detail resource.

## Fase 2 — Keamanan Kredensial

Prioritas: P1

### Pekerjaan

- [ ] Ganti AES-256-CBC menjadi AES-256-GCM atau ChaCha20-Poly1305.
- [ ] Gunakan nonce acak untuk setiap ciphertext.
- [ ] Gunakan KDF dengan salt acak dan parameter yang terdokumentasi.
- [ ] Buat format payload berversi, misalnya `v2:<salt>:<nonce>:<ciphertext>:<tag>`.
- [ ] Gunakan account ID sebagai associated data bila memungkinkan.
- [ ] Buat migrasi idempotent dari Fernet dan AES-CBC legacy ke format baru.
- [ ] Migrasikan per record dan verifikasi hasil dekripsi sebelum mengganti nilai lama.
- [ ] Jangan mencetak API key, secret, plaintext, atau encryption key ke log.
- [ ] Pastikan endpoint status hanya mengirim API key yang sudah dimasking jika memang diperlukan UI.

### Strategi migrasi

1. Backup database.
2. Deploy pembaca multi-format dan penulis format baru.
3. Migrasikan salinan database terlebih dahulu.
4. Verifikasi seluruh account credential dengan operasi read-only ke Indodax.
5. Migrasikan production.
6. Pertahankan pembaca legacy selama minimal satu release rollback.

### Kriteria selesai

- Perubahan satu byte pada ciphertext selalu menyebabkan dekripsi gagal.
- Tidak ada plaintext credential di database, response API, atau log.
- Migrasi dapat dijalankan ulang tanpa merusak record.
- Database lama masih dapat dipulihkan dengan prosedur rollback.

## Fase 3 — Autentikasi dan Session

Prioritas: P1

### Pekerjaan

- [ ] Ganti password hashing dengan Argon2id, atau naikkan parameter PBKDF2 sesuai baseline keamanan terkini.
- [ ] Tambahkan migrasi hash saat user berhasil login.
- [ ] Naikkan syarat minimum password dan dukung passphrase panjang.
- [ ] Gunakan perbandingan hash constant-time.
- [ ] Tambahkan rate limiting untuk login, register, test credential, backup, dan endpoint trading.
- [ ] Tambahkan lockout/backoff sementara setelah kegagalan login berulang.
- [ ] Pindahkan session dari `Map` ke penyimpanan persisten atau gunakan secure server-side session store.
- [ ] Hentikan penerimaan session token melalui query string.
- [ ] Utamakan cookie `HttpOnly`, `Secure`, dan `SameSite=Strict`; jika tetap menggunakan bearer token, terima hanya melalui header.
- [ ] Rotasi session saat login dan perubahan password.
- [ ] Revoke seluruh session user ketika akun dinonaktifkan atau password diganti.
- [ ] Tambahkan CSRF protection jika autentikasi memakai cookie.
- [ ] Tambahkan security headers, batas ukuran body, dan konfigurasi proxy yang eksplisit.
- [ ] Wajibkan HTTPS pada deployment publik.

### Kriteria selesai

- Session tetap valid setelah restart yang terencana, tetapi dapat direvoke oleh admin.
- Token tidak muncul dalam URL, referrer, atau access log.
- Percobaan brute force dibatasi dan tercatat.
- Perubahan password menonaktifkan session lama.

## Fase 4 — Konsolidasi Arsitektur

Prioritas: P1

### Pekerjaan

- [ ] Tetapkan arsitektur resmi: Node sebagai dashboard/API dan Python sebagai Bot Manager.
- [ ] Tetapkan `docker-entrypoint.sh` sebagai entry point production resmi.
- [ ] Putuskan status `dca_bot.py`, `indodax_client.py`, file Flask backup, dan konfigurasi legacy.
- [ ] Pindahkan komponen legacy ke direktori arsip atau hapus setelah dipastikan tidak digunakan.
- [ ] Perbaiki `python app.py` agar tidak mengimpor modul dashboard yang tidak tersedia, atau hapus klaim bahwa command tersebut menjalankan dashboard.
- [ ] Satukan seluruh default strategy, fee, RSI, dan dry-run pada satu sumber konfigurasi/schema.
- [ ] Tetapkan default baru `dry_run=true` untuk semua bot dan instalasi baru.
- [ ] Tambahkan validasi nilai strategy: nilai positif, batas jumlah SO, modal maksimum, fee, TP/SL, dan initial entry mode.
- [ ] Dokumentasikan sumber kebenaran untuk status bot dan posisi, yaitu SQLite.

### Kriteria selesai

- Hanya ada satu alur startup production yang didukung.
- Nilai default Node, Python, database, dan dokumentasi identik.
- Tidak ada entry point terdokumentasi yang gagal karena modul hilang.
- Mengubah satu strategy hanya memengaruhi bot dan pemilik terkait.

## Fase 5 — Keandalan Trading

Prioritas: P1 sebelum live, P2 untuk peningkatan lanjutan.

### Pekerjaan

- [x] Tambahkan idempotency pada pembuatan base order, SO, TP, dan SL.
- [ ] Rekonsiliasi tracked order/client ID saat startup sudah otomatis; fallback trade-history untuk order lama tanpa ID masih memerlukan validasi sandbox.
- [x] Bedakan status order `REQUESTED`, `SUBMISSION_UNKNOWN`, `OPEN`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, dan `FAILED`; `PENDING` hanya kompatibilitas legacy.
- [x] Tangani partial fill untuk base order, SO, TP, dan market sell.
- [x] Pastikan kegagalan menyimpan database setelah order exchange berhasil tidak menghasilkan order duplikat.
- [x] Validasi minimum order, precision sell, dan balance sebelum submit untuk jalur Indodax yang aktif.
- [x] Tambahkan total exposure per akun (batas maksimum modal per bot tetap tersedia).
- [x] Tambahkan circuit breaker untuk error API berulang, timestamp drift, atau state yang tidak konsisten.
- [x] Pastikan Stop membatalkan hanya order aktif yang tercatat untuk bot tersebut.
- [x] Tambahkan prosedur manual recovery untuk posisi live yang orphaned.
- [x] Gunakan timestamp UTC untuk ledger, order, posisi, siklus, dan log runtime resmi; UI mengonversinya saat ditampilkan.

### Kriteria selesai

- Restart di setiap tahap siklus tidak menghasilkan order ganda.
- Partial fill dihitung benar pada average entry, fee, TP, dan realized profit.
- Bot berhenti aman saat state exchange dan database tidak dapat direkonsiliasi.
- Semua order live dapat ditelusuri ke bot, account, cycle, dan record ledger.

## Fase 6 — Test Suite dan CI

Prioritas: P1

### Pekerjaan

- [x] Pisahkan daftar unit test Node pada `npm test` agar integration test server tetap dijalankan melalui command tersendiri.
- [x] Tambahkan command Python test yang resmi.
- [x] Pastikan dependency test terpasang di environment development/CI.
- [x] Pisahkan unit test dari integration test yang membuka server lokal.
- [x] Mock request Indodax pada unit test lifecycle order.
- [x] Tambahkan test lintas-runtime untuk encryption.
- [x] Tambahkan test autentikasi, expiry/deaktivasi, session persisten, dan admin authorization.
- [x] Tambahkan test ownership resource multi-user kritis.
- [x] Lengkapi mocked lifecycle BO/SO/TP/SL/restart, termasuk ACK hilang, partial fill, replay idempoten, dan pembatalan intent yang belum tersalin ke JSON posisi.
- [x] Tambahkan test migrasi credential pada snapshot database legacy sintetis.
- [x] Tambahkan smoke test Docker dan healthcheck.
- [x] Jalankan test otomatis pada setiap perubahan sebelum image production dibuat.

### Target minimum

- Seluruh test lulus dari checkout bersih setelah dependency dipasang.
- Test tidak membutuhkan API key asli.
- Jalur transaksi dan authorization kritis memiliki coverage langsung.
- CI gagal apabila migration, startup, atau test lintas-runtime gagal.

## Fase 7 — Observability dan Operasional

Prioritas: P2

### Pekerjaan

- [x] Ganti debug log dashboard berlebihan dengan JSON structured logging yang hanya aktif pada level `DEBUG`.
- [x] Terapkan rotasi log untuk Node dan Python (file Python memakai rotating handler; stdout keduanya dibatasi driver log container).
- [x] Redact field sensitif secara terpusat sebelum log disimpan atau dipancarkan oleh worker.
- [x] Tambahkan correlation ID untuk bot tick, order, dan DCA cycle; request HTTP memakai `X-Request-ID`.
- [x] Perluas healthcheck agar memeriksa Node, Python Bot Manager, dan koneksi database.
- [x] Tambahkan readiness check yang gagal jika SQLite tidak siap atau heartbeat worker manager mati.
- [x] Tambahkan alert untuk restart loop, kegagalan dekripsi, error exchange berulang, dan order reconciliation mismatch.
- [x] Dokumentasikan backup otomatis, retensi, verifikasi, off-host copy, dan restore drill berkala.

### Kriteria selesai

- Operator dapat mengetahui bot mana yang gagal tanpa membuka credential atau database secara manual.
- Container dianggap sehat hanya jika dashboard dan manager benar-benar berfungsi.
- Backup dan restore diuji secara terjadwal.

## Fase 8 — Rollout Live Trading

Prioritas: dilakukan hanya setelah Fase 1–6 selesai.

### Tahapan rollout

- [ ] Jalankan seluruh bot dalam dry-run minimal beberapa siklus lengkap.
- [ ] Bandingkan hasil simulasi dengan data pasar dan perhitungan manual.
- [ ] Jalankan satu akun uji dengan nominal minimum dan satu pair.
- [ ] Aktifkan batas exposure dan circuit breaker.
- [ ] Pantau satu siklus lengkap BO → SO opsional → TP/SL.
- [x] Uji restart container ketika terdapat posisi dan order terbuka pada dry-run; ledger pulih tanpa order aktif ganda.
- [ ] Perluas ke akun lain secara bertahap setelah ledger dan balance cocok.

### Go/no-go checklist

- [x] Semua test otomatis dan smoke test Docker lulus.
- [ ] Tidak ada temuan P0 atau P1 yang terbuka.
- [ ] Encryption key dan backup tersimpan aman.
- [x] Tidak ada route private yang dapat diakses tanpa autentikasi berdasarkan integration test.
- [x] Reconciliation startup dry-run berhasil.
- [x] Stop dan emergency recovery sudah diuji pada mocked exchange dan integration dry-run; kegagalan cancel mempertahankan ID untuk retry.
- [x] Nominal maksimum kerugian operasional Rp10.000 per siklus telah disetujui operator; slippage dapat membuat hasil aktual berbeda.

## Definition of Done Keseluruhan

Perbaikan dianggap selesai apabila:

- Deployment baru dapat dibuat dari checkout bersih dan startup tanpa restart loop.
- Semua data bersifat tenant-scoped dan telah diuji dengan minimal dua user.
- API credential memakai authenticated encryption dan tidak bocor melalui API/log.
- Session aman, dapat direvoke, dan tidak dikirim melalui URL.
- State order exchange dan database dapat direkonsiliasi setelah restart.
- Test Node, Python, migration, dan Docker seluruhnya lulus otomatis.
- Live trading hanya dapat diaktifkan melalui langkah eksplisit dengan batas risiko.

## Urutan Implementasi yang Disarankan

1. Fase 0 — Backup dan baseline.
2. Fase 1 — Encryption key dan proteksi route `/`.
3. Fase 2 — Migrasi authenticated encryption.
4. Fase 3 — Autentikasi dan session.
5. Fase 4 — Konsolidasi arsitektur.
6. Fase 5 — Keandalan lifecycle order.
7. Fase 6 — Test suite dan CI.
8. Fase 7 — Observability.
9. Fase 8 — Rollout live bertahap.

## Catatan Risiko

Perubahan enkripsi, reconciliation, dan lifecycle order memiliki risiko tertinggi karena berhubungan langsung dengan akses akun dan dana. Ketiga area tersebut harus dikembangkan menggunakan salinan database dan mocked exchange terlebih dahulu. Jangan menguji migrasi pertama kali pada database production atau menggunakan API key dengan izin withdrawal.
