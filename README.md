# Farm Bot Scripts (Fixed & Enhanced)

Kumpulan skrip otomatisasi proxy farm, pendaftaran akun otomatis, solver captcha/turnstile, dan bot kontrol Telegram terpadu.

## 📦 Berkas & Modul

- **`farmbot.py`**: Bot controller Telegram v2.1 dengan fitur anti-spam, navigasi in-place keyboard, auto-refresh 8 detik, pembatasan preview 12 akun terbaru, serta multi-engine live screen streaming (Xvfb :99).
- **`dashboard_v4.py`**: Daemon backend HTTP dashboard & multi-farm runner untuk monitoring status akun, kuota, dan eksekusi worker.
- **`proxy_beast_v2.py`**: High-concurrency (1.000 worker asyncio/aiohttp) HTTPS proxy scraper & validator dilengkapi MaxMind GeoIP.
- **`ps_signup_v2.py`**: Skrip otomatisasi registrasi ProxyScrape via Turnstile token & verifikasi email otomatis.
- **`solve_turnstile.py`**: Solver mandiri Cloudflare Turnstile menggunakan headless browser Camoufox.
- **`tempmail.py`**: Modul disposable email multi-provider (Mail.tm & GuerrillaMail) untuk verifikasi kode otomatis.
- **`webshare_auto.py`**: Registrasi akun Webshare via Playwright dan penyelesaian reCAPTCHA v2 berbasis CLIP vision.
- **`webshare_farm.py`**: Pendaftaran massal Webshare dan ekspor proxy otomatis dengan path virtual environment dinamis.
- **`webshare_import.py`**: Validator dan deduplikator proxy multi-threaded (100 concurrent workers).
- **`lunaproxy_farm.py`**: Otomasi registrasi dan pemanenan akun LunaProxy via disposable email.

## 🚀 Penggunaan

1. **Jalankan FarmBot Telegram**:
   ```bash
   python3 farmbot.py
   ```
2. **Jalankan Proxy Beast Validator**:
   ```bash
   python3 proxy_beast_v2.py --threads 500
   ```
3. **Pecahkan Cloudflare Turnstile**:
   ```bash
   python3 solve_turnstile.py <URL_TARGET> [SITEKEY]
   ```
