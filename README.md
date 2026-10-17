# Farm Bot Scripts (Fixed, Enhanced & Enterprise Ready)

Kumpulan skrip otomatisasi proxy farm, pendaftaran akun otomatis, solver captcha/turnstile, dan bot kontrol Telegram terpadu.

## 📦 Berkas & Modul Utama

- **`proxyscrape_harvester.py`**: **[BARU]** Multi-Worker Harvester untuk ProxyScrape (Producer-Consumer architecture) terintegrasi dengan **Mail.tm & TempMail API** tanpa memerlukan domain kustom. Otomatis menyelesaikan Cloudflare Turnstile, memverifikasi OTP, mengklaim Trial 7 Hari, dan mengekspor proxy terautentikasi ke `output/proxies_http.txt`.
- **`farmbot.py`**: Bot controller Telegram v2.1 dengan fitur anti-spam, navigasi in-place keyboard, auto-refresh 8 detik, pembatasan preview 12 akun terbaru, serta multi-engine live screen streaming (Xvfb :99).
- **`dashboard_v4.py`**: Daemon backend HTTP dashboard & multi-farm runner untuk monitoring status akun, kuota, dan eksekusi worker.
- **`proxy_beast_v2.py`**: High-concurrency (1.000 worker asyncio/aiohttp) HTTPS proxy scraper & validator dilengkapi MaxMind GeoIP.
- **`ps_signup_v2.py`**: Skrip otomatisasi registrasi ProxyScrape versi single-run.
- **`solve_turnstile.py`**: Solver mandiri Cloudflare Turnstile menggunakan headless browser.
- **`tempmail.py`**: Modul disposable email multi-provider (Mail.tm, TempMail.plus) untuk verifikasi kode otomatis.
- **`webshare_auto.py`**: Registrasi akun Webshare via Playwright dan penyelesaian reCAPTCHA v2 berbasis CLIP vision.
- **`webshare_farm.py`**: Pendaftaran massal Webshare dan ekspor proxy otomatis.
- **`webshare_import.py`**: Validator dan deduplikator proxy multi-threaded (100 concurrent workers).
- **`lunaproxy_farm.py`**: Otomasi registrasi dan pemanenan akun LunaProxy via disposable email.
- **`cloudflare_worker/`**: Modul opsional Cloudflare Email Routing & Webhook Receiver bagi pengguna yang memiliki domain kustom sendiri.

---

## 🚀 Penggunaan

### 1. Jalankan ProxyScrape Harvester (Mail.tm / TempMail):
```bash
# Panen 10 akun ProxyScrape secara paralel tanpa domain:
python3 proxyscrape_harvester.py --count 10 --workers 2 --consumers 3
```
Hasil proxy akan otomatis tersimpan di `output/proxies_http.txt` dalam format:
```text
http://username:password@ip:port
```

### 2. Jalankan FarmBot Telegram:
```bash
python3 farmbot.py
```

### 3. Jalankan Proxy Beast Validator:
```bash
python3 proxy_beast_v2.py --threads 500
```
