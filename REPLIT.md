# Melanjutkan Bayproject di Replit

Paket ini menyiapkan **pengujian backend di workspace Replit**. Belum menyelesaikan deployment produksi atau pembuatan APK/IPA.

## Persiapan workspace

1. Cadangkan proyek bot yang ada. Impor source aplikasi ke proyek pengujian atau gabungkan folder `backend`, `mobile`, dan `scripts`. Jangan menimpa konfigurasi bot yang sedang berjalan tanpa memeriksa perbedaan `.replit` terlebih dahulu.
2. Pilih runtime Python 3.12. Dari Shell root proyek: `python -m pip install -r requirements.txt`.
3. Tambahkan `APP_ACCESS_TOKEN` di Secrets, memakai nilai acak minimal 32 karakter. Jika memakai `scripts/setup_private.py`, salin nilai dari `.env` ke Secrets sendiri; file itu tidak masuk paket source.
4. Tambahkan `AI_API_KEY` (atau `GROQ_API_KEY` lama), `AI_BASE_URL`, `AI_TEXT_MODEL`, `AI_VISION_MODEL`, dan API sumber data yang digunakan. Nilai Groq default dan contoh 9Router ada di `backend/.env.example`. Jangan kirim API key melalui chat.
5. Jalankan `python scripts/run_replit.py --check`, lalu tombol Run. Script `.replit` mengarahkan Run ke API mobile, bukan `backend/main.py` yang menjalankan bot Telegram/Discord lama.
6. API mendengarkan port 8000. Request `/health` memerlukan kunci Bearer; respons 401 tanpa kunci bukan bukti server gagal. Gunakan URL HTTPS workspace saat menguji aplikasi. URL workspace bukan pengganti deployment yang selalu aktif.

## Penyimpanan sebelum publishing

Kode saat ini masih memakai SQLite lokal. **Mengubah `DB_PATH` ke folder biasa di Replit tidak menjadikannya persisten.** Filesystem aplikasi published dapat direset saat publish, sehingga diperlukan integrasi database persisten sebelum menyimpan riwayat/alert produksi. Hal ini juga berlaku bila proses memakai Reserved VM.

Langkah berikutnya adalah menghubungkan database persisten (misalnya PostgreSQL yang disediakan proyek Replit), memigrasikan tabel, dan menguji data tetap ada setelah restart/redeploy. Adapter PostgreSQL belum diimplementasikan; memasukkan `DATABASE_URL` sekarang saja belum mengubah penyimpanan.

Reserved VM cocok untuk proses scheduler yang terus berjalan. Pemilihan layanan dan publishing belum dilakukan. Backend tetap membutuhkan autentikasi aplikasi meskipun endpoint HTTPS bisa dijangkau HP.

Sumber: [Replit — troubleshooting penyimpanan published app](https://docs.replit.com/build/troubleshooting), [pilihan deployment Replit](https://docs.replit.com/learn/projects-and-artifacts/replit-deployments).

## Yang dibutuhkan untuk melanjutkan proyek yang ada

- URL workspace proyek Replit agar struktur dan konfigurasi existing dapat diperiksa.
- API key tetap dimasukkan sendiri ke Replit Secrets.
- Ketersediaan database persisten pada proyek; jangan menyalin connection string ke chat.
- Akun Expo untuk build Android dan kredensial Apple untuk distribusi iOS, pada tahap build HP nanti.
