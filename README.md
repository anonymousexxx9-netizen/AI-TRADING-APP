# Bayproject — aplikasi pribadi Android / iOS

Aplikasi React Native/Expo yang memakai kembali mesin Python dari `bot_4.zip`. Seluruh kelompok fitur bot diberi antarmuka mobile dan API pribadi. Database bawaan ZIP tidak disalin, sehingga data pengguna Telegram/Discord lama tidak masuk ke aplikasi secara otomatis.

**Status:** implementasi aplikasi dan backend tersedia; tes otomatis dan kompilasi bundle dilakukan secara lokal. Belum ada APK/IPA bertanda tangan, deployment server, atau pengujian provider AI/push pada HP fisik. API key, alamat backend HTTPS, serta akun signing diisi oleh pemilik.

## Struktur

```text
mobile/                  Aplikasi Android/iOS, Expo SDK 55 + TypeScript
  App.tsx                Dashboard, pasar, chat, toolkit, inbox, settings
  src/api.ts             API client & SecureStore
  src/push.ts            Pendaftaran Expo push token
  eas.json               Profil development / APK internal / production
backend/
  api.py                 API pribadi, validasi, autentikasi
  core.py                Mesin analisis dari bot asli
  ai_gateway.py          Transport Groq / 9Router / OpenAI-compatible
  worker.py              Scheduler mobile & pengiriman push
  storage.py             Riwayat chat permanen, inbox, antrean push
  telegram_bot.py         Handler Telegram asli
  discord_bot.py          Handler Discord asli
  main.py                Entry point bot lama (terpisah)
  tests/                 Pengujian tanpa request eksternal
compose.yaml             Backend dengan volume database persisten
```

## Menjalankan backend

Untuk menyiapkan konfigurasi pribadi tanpa menimpa file yang sudah ada, jalankan `python scripts/setup_private.py` dari root. Script membuat `backend/.env` dengan kunci acak, tetapi tidak mengisi API key AI. Setelah dependency terpasang, jalankan `python scripts/check_readiness.py` untuk melihat kelengkapan konfigurasi tanpa mencetak rahasia. Jika memakai script setup ini, lewati langkah `Copy-Item` di bawah.

Gunakan Python 3.12. Dari folder proyek:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-dev.txt
Copy-Item backend/.env.example backend/.env
```

Buat kunci akses pribadi dengan `python -c "import secrets; print(secrets.token_urlsafe(32))"`, lalu masukkan hasilnya ke `APP_ACCESS_TOKEN` di `backend/.env`. Isi `AI_API_KEY`, model yang tersedia di akunmu, serta API key sumber data bila diperlukan. Jangan memasukkan API key AI ke aplikasi atau ke repositori.

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8000 --workers 1
```

API memerlukan header `Authorization: Bearer <APP_ACCESS_TOKEN>` untuk setiap endpoint. `/health` juga terlindungi dan tidak membuktikan provider AI berhasil dihubungi; hanya menyatakan konfigurasi tersedia.

Untuk server deployment, jalankan perintah yang sama dengan `--host 0.0.0.0`, dan gunakan HTTPS dari hosting/reverse proxy. Untuk Replit, ikuti `REPLIT.md`: perintah workspace sudah disiapkan, tetapi database persisten perlu diintegrasikan sebelum publishing. Filesystem published Replit tidak persisten; mengubah `DB_PATH` ke folder biasa tidak cukup. Simpan rahasia di Replit Secrets. Workspace editor saja tidak menjamin scheduler berjalan terus.

Alternatif Docker: setelah `backend/.env` terisi, jalankan `docker compose up --build -d`. Port host dibatasi ke `127.0.0.1:8000`; reverse proxy HTTPS dapat meneruskan request ke port tersebut. Volume `bot-data` mempertahankan SQLite. Jalankan **satu replica / satu worker Uvicorn per database** untuk menghindari scheduler ganda.

## Menjalankan aplikasi

Gunakan Node.js 22 LTS atau versi kompatibel SDK Expo.

```powershell
cd mobile
npm ci
npm run start
```

Masukkan alamat backend HTTPS dan kunci `APP_ACCESS_TOKEN` pada layar koneksi. HTTP hanya diizinkan untuk loopback (`localhost`, `127.0.0.1`, `10.0.2.2`) untuk pengujian lokal. Di HP fisik, `localhost` menunjuk HP itu sendiri, bukan komputer/Replit. Gunakan URL HTTPS yang dapat dijangkau HP.

Kode akses disimpan dalam SecureStore pada Android/iOS. Preview browser menyimpan koneksi hanya selama sesi halaman. Riwayat AI tersimpan di SQLite server; gambar dikirim untuk dianalisis tetapi tidak disimpan sebagai file oleh aplikasi backend.

## Membuat APK dan aplikasi iPhone

Konfigurasi `mobile/eas.json` sudah disiapkan. Proses berikut memerlukan login akunmu dan dapat memakai kuota build akun tersebut; belum dijalankan dalam pekerjaan ini.

1. Dari `mobile`, jalankan `npx eas-cli login`, kemudian `npx eas-cli init` untuk menghubungkan proyek dengan akun Expo. Karena konfigurasi dinamis, isi project ID yang diperoleh melalui `EXPO_PUBLIC_EAS_PROJECT_ID` di environment build/local `.env`, atau tetapkan ID tersebut dalam `app.config.ts`.
2. Sesuaikan `ios.bundleIdentifier` dan `android.package` bila identifier contoh sudah dipakai akun lain. Jangan mengubah identifier setelah signing tanpa menyesuaikan kredensial terkait.
3. **Android pribadi:** `npx eas-cli build --platform android --profile preview`. Profil ini menghasilkan APK yang dapat dipasang melalui tautan build.
4. **iPhone pribadi:** daftarkan iPhone melalui `npx eas-cli device:create`, lalu `npx eas-cli build --platform ios --profile preview`. Jalur ad hoc ini memerlukan kredensial Apple Developer dan provisioning perangkat.
5. Untuk TestFlight, gunakan profil `production`, lalu submit melalui akun Apple milikmu. Tidak perlu publikasi App Store untuk pengujian pribadi.

Build native iOS lokal memerlukan macOS/Xcode. Kompilasi bundle JavaScript/Hermes di Windows bukan pengganti native signing atau pengujian di iPhone.

Referensi: [distribusi internal Expo](https://docs.expo.dev/build/internal-distribution/), [TestFlight](https://docs.expo.dev/submit/testflight/).

## Push notification

Inbox selalu tersedia melalui API. Push membutuhkan konfigurasi tambahan:

- Hubungkan EAS project ID sebelum build.
- Android: siapkan Firebase/FCM v1. Gunakan environment `GOOGLE_SERVICES_JSON` (path file lokal atau file environment EAS) untuk `google-services.json`; upload service-account credential FCM ke pengaturan EAS, bukan ke repositori.
- iOS: siapkan APNs credentials dan provisioning melalui EAS/Apple.
- Install development build atau APK/ad hoc build di HP fisik. Expo Go bukan jalur pengujian push untuk proyek ini.
- Buka **Pengaturan → Aktifkan push di HP ini**, lalu **Kirim notifikasi uji**. Pastikan `ENABLE_WORKER=true`.

Notifikasi masuk ke SQLite terlebih dahulu. Worker mencoba mengirim ke Expo, menyimpan ticket, dan memeriksa receipt. Kegagalan request dicoba ulang hingga lima kali; inbox tetap menyimpan isi lengkap. Status `delivered` berarti provider push menerima pengiriman, bukan bukti pengguna membacanya. `mobile_deliveries` mencatat status/error untuk diagnosis. Push melewati Expo dan FCM/APNs; isi preview dibatasi 180 karakter.

Worker memeriksa harga/kalender kira-kira setiap menit setelah siklus sebelumnya selesai; scan/trap/volatilitas kira-kira setiap 15 menit. Panggilan data/AI yang lambat dapat menambah jeda. Slot laporan mengikuti WIB dari bot (07:00, 14:00, 19:30; debrief 05:00). Slot yang terlewat dikejar hingga dua jam dan deduplikasi tersimpan setelah restart. Kalender sumber tetap berpatokan America/New_York, termasuk filter “hari ini”. Jadwal sesi ini mengikuti jam tetap bot, bukan penyesuaian DST otomatis.

Referensi: [setup push Expo](https://docs.expo.dev/push-notifications/push-notifications-setup/), [tickets dan receipts](https://docs.expo.dev/push-notifications/sending-notifications/).

## Groq sekarang, 9Router nanti

Semua panggilan AI teks, agent/tool calling, dan vision melewati `ai_gateway.py`. Nama fungsi Groq dalam core dipertahankan agar kompatibel dengan handler lama, tetapi endpoint sudah tidak ditulis langsung di fungsi tersebut.

```dotenv
AI_BASE_URL=https://your-private-router.example/v1
AI_API_KEY=<kunci dari dashboard router>
AI_TEXT_MODEL=<model teks yang mendukung tool calling>
AI_VISION_MODEL=<model yang mendukung gambar>
AI_REASONING_EFFORT=
```

Restart backend setelah mengganti konfigurasi. Nama model awal berasal dari ZIP, bukan jaminan model tersedia pada akun saat ini. Pilih ID yang benar di dashboard provider. Kosongkan `AI_REASONING_EFFORT` kecuali model secara eksplisit mendukungnya. AI key/model tidak dapat diubah melalui endpoint publik aplikasi.

9Router di komputer pribadi harus dapat dijangkau backend server; `localhost` pada Replit bukan komputer pribadi. Jalankan router pada jaringan server yang sesuai dan lindungi endpoint dengan autentikasi serta HTTPS. Router tidak menggantikan sumber harga/OHLC, kalender, atau berita. Pengujian live harus mencakup chat, tool membuat alert, unggah gambar, dan kegagalan provider. [Dokumentasi 9Router](https://github.com/decolua/9router/blob/master/README.md).

## Cakupan fitur

| Fitur bot | Lokasi aplikasi |
|---|---|
| Harga, watch/unwatch/watchlist | Beranda |
| Chart candle, EMA, support/resistance | Pasar → Analisis / chart lengkap |
| Regime, confidence score, indikator | Pasar → Ringkasan / Score / Indikator |
| Pattern, market structure, trap | Pasar → Pattern / Structure / Ringkasan |
| Confluence lintas timeframe | Pasar → Confluence |
| AI penjelasan teknikal | Pasar → Penjelasan teknikal oleh AI |
| Scanner dan entry khusus XAUUSD | Toolkit → Sinyal |
| Backtest scanner | Toolkit → Backtest |
| Kalkulator lot dan korelasi | Toolkit → Kalkulator lot / Korelasi |
| Kalender, high impact, bias, event preview, actual fallback | Toolkit → Kalender |
| Berita, macro briefing, daily debrief | Toolkit → Berita & Makro |
| Price alert, langganan macro/session/debrief | Inbox → Price alert / Langganan |
| Auto-signal, trap, volatilitas, high impact | Worker → Inbox / push |
| Chat agent dan unggah chart | Assistant |
| Reset percakapan | Pengaturan |
| Telegram / Discord | Kode handler asli dipertahankan; belum disambungkan ke identitas mobile |

Untuk tetap memakai bot lama, jalankan `backend/main.py` secara terpisah dengan `TELEGRAM_TOKEN`, `DISCORD_TOKEN`, dan **database tersendiri**. Jangan menjalankan scheduler lama dan worker API terhadap database yang sama: selain duplikasi jadwal, worker mobile tidak menyiapkan klien Telegram/Discord. Data watchlist/langganan bot lama belum dimigrasikan ke identitas mobile. Konfigurasi channel Discord tetap melalui command bot.

Tidak ada eksekusi order broker. Kalkulator dan backtest memakai asumsi dari bot asli. Chat agent memiliki satu putaran tool calling lalu ringkasan; belum berupa agent otonom tanpa batas. Harga di dashboard diambil saat refresh, bukan streaming. Layanan sumber data bisa gagal atau memberikan cache lama; pemeriksaan akhir di HP harus membandingkan timestamp candle dan sumber data.

## Pengujian dan preview

```powershell
# Dari root
.\.venv\Scripts\python.exe -m pytest backend/tests -q
cd mobile
npm run typecheck
npx expo install --check
npx expo export --platform all
```

Tes menggunakan data fixture dan mock provider; tidak mengirim pesan Telegram/Discord, melakukan trade, atau memakai API key asli. Cakupan termasuk autentikasi, CRUD, validasi gambar, persistence, tool calling, fallback konfirmasi, alert saat delivery gagal, deduplikasi jadwal, receipt push, dan mesin chart/backtest asli.

Untuk melihat UI React Native melalui browser lokal, export web lalu jalankan `python backend/preview.py` dari root. Buka `http://127.0.0.1:8000`, gunakan URL tersebut sebagai alamat server dan kunci dari `.local-preview/access.txt`. Script membuat database preview terpisah, key acak, dan menonaktifkan scheduler. Bukan entry point deployment. Tampilan browser membantu QA layout, tetapi bukan pengujian native permissions/push/keyboard di Android/iOS.

`mobile/package-lock.json` menyimpan versi Node yang diuji. Override `xcode → uuid ^11.1.1` memperbaiki advisory dependency UUID pada alat build; pemakaian `uuid.v4()` Xcode tetap kompatibel. `backend/requirements-lock.txt` merekam environment Python pengujian. Gunakan requirements berjenjang untuk instalasi lintas platform atau lock tersebut untuk mereproduksi versi pengujian.
