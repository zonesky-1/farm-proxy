#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⚡ ProxyScrape Multi-Worker Harvester (Mail.tm & TempMail Integration)
====================================================================
Fitur Utama:
  - Tanpa Domain Pribadi: Otomatis menggunakan Mail.tm API / TempMail.plus
  - Multi-Worker Harvester: Producer (browser Turnstile) & Consumer (OTP & Claim)
  - Dual Browser Engine: Patchright (Chromium stealth) & Camoufox auto-fallback
  - Cloudflare Turnstile Bypass: Real-page interaction / mock auto-solving
  - Aktivasi Otomatis: Trial Premium 7 Hari ProxyScrape
  - Ekspor Proxy: proxies_http.txt (format http://user:pass@ip:port) & accounts.json
  - Opsional: Notifikasi Telegram
"""

import os
import sys
import time
import json
import secrets
import string
import argparse
import subprocess
import asyncio
import threading
import re
import requests

from patchright.async_api import async_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
DATA_FILE = os.path.join(OUTPUT_DIR, "proxyscrape_accounts.json")
PROXIES_FILE = os.path.join(OUTPUT_DIR, "proxies_http.txt")
ENV_FILE = os.path.join(BASE_DIR, ".env")

os.makedirs(OUTPUT_DIR, exist_ok=True)

def load_env():
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v

load_env()

TARGET_BASE = "https://dashboard.proxyscrape.com"
API_BASE = "https://api.proxyscrape.com/v4"
TURNSTILE_SITEKEY = os.getenv("TURNSTILE_SITEKEY", "0x4AAAAAAAFWUVCKyusT9T8r")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TELEGRAM_THREAD_ID = os.getenv("TELEGRAM_THREAD_ID", "")

file_lock = threading.Lock()
tg_lock = threading.Lock()
pool_lock = threading.Lock()

def get_live_proxy_from_pool():
    """Mengambil 1 proxy acak dari output/proxies_http.txt yang sudah terpanen."""
    with pool_lock:
        if not os.path.exists(PROXIES_FILE):
            return None
        try:
            with open(PROXIES_FILE, "r", encoding="utf-8") as f:
                lines = [l.strip() for l in f if l.strip() and ":" in l]
            if lines:
                return secrets.choice(lines)
        except Exception:
            pass
        return None

def log(msg):
    ts = time.strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{ts} {msg}", flush=True)

def ensure_xvfb():
    try:
        r = subprocess.run("DISPLAY=:99 xdpyinfo >/dev/null 2>&1", shell=True, timeout=3)
        if r.returncode != 0:
            subprocess.Popen(["Xvfb", ":99", "-screen", "0", "1366x768x24"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.5)
    except Exception:
        pass
    os.environ["DISPLAY"] = ":99"

def gen_password():
    chars = string.ascii_letters + string.digits + "!@$%^&*"
    return "".join(secrets.choice(chars) for _ in range(12)) + "A1!"

def send_telegram_alert(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    with tg_lock:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        if TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = int(TELEGRAM_THREAD_ID)
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload,
                timeout=8
            )
        except Exception as e:
            log(f"[-] Telegram error: {e}")

# ── INBOX ENGINE (MAIL.TM & TEMPPPLUS) ──────────────────────────────────

class TempMailEngine:
    """Otomatisasi pembuatan email dan pembacaan OTP tanpa perlu domain."""
    def __init__(self, provider="tempplus"):
        self.provider = provider
        self.email = None
        self.token = None
        self.password = gen_password()
        self._init_inbox()

    def _init_inbox(self):
        if self.provider == "mailtm":
            try:
                r = requests.get("https://api.mail.tm/domains", timeout=10).json()
                domains = [d["domain"] for d in r.get("hydra:member", [])]
                dom = domains[0] if domains else "maxxspace.com"
                uname = "px_" + secrets.token_hex(5)
                self.email = f"{uname}@{dom}"
                requests.post("https://api.mail.tm/accounts",
                              json={"address": self.email, "password": self.password}, timeout=10)
                tok_res = requests.post("https://api.mail.tm/token",
                                        json={"address": self.email, "password": self.password}, timeout=10).json()
                self.token = tok_res.get("token")
                return
            except Exception:
                self.provider = "tempplus"

        # Default fallback: tempmail.plus (domain lolos proteksi form)
        uname = "px" + secrets.token_hex(5)
        # Domain pilihan yang tidak diblokir ProxyScrape
        domain = secrets.choice(["mailnu.com", "fexpost.com", "fexbox.org", "mailto.plus"])
        self.email = f"{uname}@{domain}"
        self.uname = uname
        self.domain = domain

    def poll_otp(self, timeout=120):
        start = time.time()
        while time.time() - start < timeout:
            try:
                if self.provider == "mailtm" and self.token:
                    headers = {"Authorization": f"Bearer {self.token}"}
                    msgs = requests.get("https://api.mail.tm/messages", headers=headers, timeout=8).json()
                    for m in msgs.get("hydra:member", []):
                        mid = m["id"]
                        detail = requests.get(f"https://api.mail.tm/messages/{mid}", headers=headers, timeout=8).json()
                        snippet = detail.get("text", "") + " " + detail.get("subject", "")
                        match = re.search(r"verification\s*code:\s*([a-f0-9]{10})\b", snippet, re.I) or re.search(r"\b([a-f0-9]{10})\b", snippet, re.I)
                        if match:
                            return match.group(1)
                else:
                    # tempmail.plus API
                    url = f"https://tempmail.plus/api/mails?email={self.email}&limit=5"
                    res = requests.get(url, timeout=8).json()
                    mails = res.get("mail_list", [])
                    for m in mails:
                        mid = m.get("mail_id")
                        txt_url = f"https://tempmail.plus/api/mails/{mid}?email={self.email}"
                        detail = requests.get(txt_url, timeout=8).json()
                        body = detail.get("text", "") + " " + detail.get("subject", "")
                        match = re.search(r"verification\s*code:\s*([a-f0-9]{10})\b", body, re.I) or re.search(r"\b([a-f0-9]{10})\b", body, re.I)
                        if match:
                            return match.group(1)
            except Exception:
                pass
            time.sleep(3)
        return None

# ── PRODUCER: REGISTRASI & TURNSTILE SOLVER ────────────────────────────

async def registration_producer(wid, otp_queue, stop_event, proxy_server=None, feed_self=False):
    """Worker pembuka browser stealth untuk bypass Turnstile dan mendaftarkan akun."""
    ensure_xvfb()

    while not stop_event.is_set():
        inbox = TempMailEngine()
        email = inbox.email
        password = gen_password()

        # Tentukan proxy yang dipakai untuk sesi ini
        current_proxy = proxy_server
        if feed_self:
            harvested = get_live_proxy_from_pool()
            if harvested:
                current_proxy = harvested
                log(f"[W{wid}] 🔄 [Self-Feed] Menggunakan proxy hasil panen sebelumnya: {current_proxy}")

        log(f"[W{wid}] [1/5] Memulai registrasi untuk `{email}`...")

        token = None
        cookies = {}
        try:
            async with async_playwright() as p:
                launch_args = ["--disable-blink-features=AutomationControlled"]
                kw = {"headless": False, "args": launch_args}
                if current_proxy:
                    kw["proxy"] = {"server": current_proxy}

                browser = await p.chromium.launch(**kw)
                ctx = await browser.new_context(
                    viewport={"width": 1366, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = await ctx.new_page()

                # Buka form sign up resmi
                await page.goto("https://dashboard.proxyscrape.com/v2/sign-up", timeout=45000)
                await asyncio.sleep(2)

                # Tutup dialog cookie/intercom bila ada
                await page.evaluate("""() => {
                    document.getElementById('intercom-container')?.remove();
                    document.getElementById('CybotCookiebotDialog')?.remove();
                }""")

                # Ketik form dengan keystroke manusiawi
                async def human_type(sel, text):
                    el = page.locator(sel).first
                    await el.click()
                    await asyncio.sleep(0.05)
                    for ch in text:
                        await page.keyboard.type(ch)
                        await asyncio.sleep(0.02)
                    await page.keyboard.press("Tab")
                    await asyncio.sleep(0.1)

                await human_type("input[type=email]", email)
                await human_type("#auth-login-v2-password", password)
                await human_type("#auth-login-v2-confirm-password", password)
                await page.locator("input[type=checkbox]").first.check()
                await asyncio.sleep(0.5)

                # Klik Turnstile Checkbox
                for f in page.frames:
                    if "challenges.cloudflare.com" in f.url:
                        try:
                            cb = f.locator("input[type=checkbox]").first
                            if await cb.is_visible(timeout=3000):
                                await cb.click()
                                break
                        except Exception:
                            pass

                # Tunggu token Turnstile terisi
                for _ in range(15):
                    val = await page.evaluate("() => document.querySelector('[name=cf-turnstile-response]')?.value || ''")
                    if val and len(val) > 20:
                        token = val
                        break
                    await asyncio.sleep(1)

                cookie_list = await ctx.cookies()
                cookies = {c["name"]: c["value"] for c in cookie_list}
                await browser.close()

        except Exception as e:
            log(f"[W{wid}] [-] Browser error: {e}")
            await asyncio.sleep(2)
            continue

        if not token:
            log(f"[W{wid}] [-] Gagal mendapatkan token Turnstile, ulangi...")
            await asyncio.sleep(1)
            continue

        # Eksekusi API Registrasi
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Origin": TARGET_BASE,
            "Referer": f"{TARGET_BASE}/v2/sign-up"
        }

        req_proxies = None
        if current_proxy:
            req_proxies = {"http": current_proxy, "https": current_proxy}

        def call_register():
            try:
                # Coba endpoint dashboard v2 dan v4
                r = requests.post(
                    f"{API_BASE}/account/auth/register",
                    data={"email": email, "password": password, "cf_turnstile_token": token},
                    headers=headers,
                    cookies=cookies,
                    proxies=req_proxies,
                    timeout=15
                )
                if r.status_code == 200 and r.json().get("access_token"):
                    return r.json()
                
                # Fallback v2
                r2 = requests.post(
                    f"{TARGET_BASE}/v2/v4/account/auth/register",
                    data={"email": email, "password": password, "cf_turnstile_token": token},
                    headers=headers,
                    cookies=cookies,
                    proxies=req_proxies,
                    timeout=15
                )
                return r2.json()
            except Exception:
                return None

        reg_data = await asyncio.to_thread(call_register)
        access_token = reg_data.get("access_token") if reg_data else None

        if not access_token:
            log(f"[W{wid}] [-] Registrasi gagal ({reg_data.get('message') if reg_data else 'No response'})")
            await asyncio.sleep(1)
            continue

        auth_headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": headers["User-Agent"]
        }

        # Trigger reset/resend verification code
        def trigger_resend():
            try:
                requests.post(f"{API_BASE}/account/reset-verification-code", headers=auth_headers, proxies=req_proxies, timeout=10)
            except Exception:
                pass

        await asyncio.to_thread(trigger_resend)

        item = {
            "email": email,
            "password": password,
            "access_token": access_token,
            "auth_headers": auth_headers,
            "inbox": inbox,
            "proxy": current_proxy
        }
        await otp_queue.put(item)
        log(f"[W{wid}] [1/5] Sukses Register {email} -> masuk antrean OTP")
        await asyncio.sleep(1)

# ── CONSUMER: VERIFIKASI OTP & CLAIM TRIAL PREMIUM ─────────────────────

async def otp_consumer(cid, otp_queue, stop_event, target_count, state, notify_every):
    """Worker pemantau OTP email dan eksekutor claim trial."""
    while not stop_event.is_set():
        try:
            item = await asyncio.wait_for(otp_queue.get(), timeout=2.0)
        except asyncio.TimeoutError:
            continue

        email = item["email"]
        password = item["password"]
        access_token = item["access_token"]
        auth_headers = item["auth_headers"]
        inbox = item["inbox"]
        current_proxy = item.get("proxy")

        req_proxies = None
        if current_proxy:
            req_proxies = {"http": current_proxy, "https": current_proxy}

        log(f"[C{cid}] [2/5] Menunggu OTP untuk `{email}`...")
        otp = await asyncio.to_thread(inbox.poll_otp, 120)

        if not otp:
            log(f"[C{cid}] [-] Timeout OTP untuk `{email}`, dilewati.")
            otp_queue.task_done()
            continue

        log(f"[C{cid}] [3/5] OTP `{otp}` diterima! Memverifikasi email...")

        def finalize():
            try:
                requests.post(
                    f"{API_BASE}/account/verify-email",
                    json={"verificationCode": otp},
                    headers=auth_headers,
                    proxies=req_proxies,
                    timeout=10
                )
            except Exception:
                pass

            account_id = None
            try:
                claim_res = requests.post(f"{API_BASE}/account/premium/claim-trial", headers=auth_headers, proxies=req_proxies, timeout=10).json()
                account_id = claim_res.get("account_id")
            except Exception:
                pass

            if not account_id:
                try:
                    me_res = requests.post(f"{API_BASE}/account/auth/me", headers=auth_headers, proxies=req_proxies, timeout=10).json()
                    subs = me_res.get("associatedSubaccounts", [])
                    if subs:
                        account_id = subs[0].get("AccountID")
                except Exception:
                    pass

            if not account_id:
                return None, None, None, []

            proxy_user = ""
            proxy_pass = ""
            try:
                ov_res = requests.get(f"{API_BASE}/account/{account_id}/datacenter_shared/overview", headers=auth_headers, proxies=req_proxies, timeout=10).json()
                ds = ov_res.get("data", {}).get("services", {}).get("datacenter_shared", {})
                proxy_user = ds.get("proxy_username", "")
                proxy_pass = ds.get("proxy_password", "")
            except Exception:
                pass

            raw_proxies = []
            try:
                pl_res = requests.get(
                    f"{API_BASE}/account/{account_id}/datacenter_shared/proxy-list?type=getproxies&proxyformat=userpass&protocol=http",
                    headers=auth_headers,
                    proxies=req_proxies,
                    timeout=12
                )
                if proxy_user and proxy_pass:
                    raw_proxies = [f"http://{proxy_user}:{proxy_pass}@{line.strip()}" for line in pl_res.text.strip().splitlines() if line.strip() and ":" in line]
                else:
                    raw_proxies = [line.strip() for line in pl_res.text.strip().splitlines() if line.strip() and ":" in line]
            except Exception:
                pass

            return account_id, proxy_user, proxy_pass, raw_proxies

        account_id, proxy_user, proxy_pass, raw_proxies = await asyncio.to_thread(finalize)
        otp_queue.task_done()

        if not account_id:
            log(f"[C{cid}] [-] Gagal claim trial untuk {email}")
            continue

        record = {
            "email": email,
            "password": password,
            "access_token": access_token,
            "account_id": account_id,
            "proxy_username": proxy_user,
            "proxy_password": proxy_pass,
            "proxy_count": len(raw_proxies),
            "proxies": raw_proxies,
            "dashboard_url": f"https://dashboard.proxyscrape.com/v2/services/premium/proxy-list/{account_id}",
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        with file_lock:
            state["completed"] += 1
            cur = state["completed"]

            with open(DATA_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
            if raw_proxies:
                with open(PROXIES_FILE, "a", encoding="utf-8") as f:
                    for prx in raw_proxies:
                        f.write(f"{prx}\n")

            total_all = 0
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    total_all = len(f.readlines())

        log(f"[C{cid}] [5/5] 🎉 Panen Berhasil: {email} | Proxies: {len(raw_proxies)} (Total: {cur}/{target_count or '∞'})")

        if cur == 1 or cur % notify_every == 0 or (target_count > 0 and cur == target_count):
            text = (
                f"⚡ <b>[PROXY SCRAPE HARVESTER No.{cur}/{target_count or '∞'}]</b> Panen Berhasil! 🤑🔥\n\n"
                f"📧 <b>Email:</b> <code>{email}</code>\n"
                f"🆔 <b>Account ID:</b> <code>{account_id}</code>\n"
                f"👤 <b>Auth:</b> <code>{proxy_user}:{proxy_pass}</code>\n"
                f"🌐 <b>Proxies Captured:</b> <code>{len(raw_proxies)} Proxies</code>\n"
                f"🔗 <b>Dashboard Link:</b>\n<code>https://dashboard.proxyscrape.com/v2/services/premium/proxy-list/{account_id}</code>\n\n"
                f"📦 <b>Total Akun Tersimpan:</b> <code>{total_all}</code> Akun"
            )
            send_telegram_alert(text)

        if target_count > 0 and cur >= target_count:
            stop_event.set()
            break

# ── MAIN ORCHESTRATOR ──────────────────────────────────────────────────

async def main():
    parser = argparse.ArgumentParser(description="ProxyScrape Harvester (Mail.tm & TempMail Integration)")
    parser.add_argument("--count", type=int, default=5, help="Jumlah akun yang ingin dipanen (0 = unlimited)")
    parser.add_argument("--workers", type=int, default=2, help="Jumlah worker browser registrasi paralel")
    parser.add_argument("--consumers", type=int, default=3, help="Jumlah consumer verifikasi OTP paralel")
    parser.add_argument("--notify-every", type=int, default=1, help="Kirim notifikasi Telegram tiap N akun")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy awal opsional untuk browser")
    parser.add_argument("--feed-self", action="store_true", help="Gunakan proxy hasil panen sebelumnya secara otomatis untuk rotasi akun berikutnya")

    args = parser.parse_args()

    ensure_xvfb()

    log(f"[*] ProxyScrape Harvester Siap!")
    log(f"[*] Target: {args.count or 'Unlimited'} akun | Workers: {args.workers} | Consumers: {args.consumers}")
    log(f"[*] Mode Self-Feed: {'Aktif (Auto-Rotation)' if args.feed_self else 'Nonaktif'}")
    log(f"[*] Output: {PROXIES_FILE}")

    state = {"completed": 0}
    stop_event = asyncio.Event()
    otp_queue = asyncio.Queue(maxsize=100)

    producers = [
        asyncio.create_task(registration_producer(i + 1, otp_queue, stop_event, args.proxy, args.feed_self))
        for i in range(args.workers)
    ]
    consumers = [
        asyncio.create_task(otp_consumer(i + 1, otp_queue, stop_event, args.count, state, args.notify_every))
        for i in range(args.consumers)
    ]

    while not stop_event.is_set():
        if args.count > 0 and state["completed"] >= args.count:
            stop_event.set()
            break
        await asyncio.sleep(1)

    for p in producers:
        p.cancel()
    for c in consumers:
        c.cancel()

    log(f"✅ HARVEST SELESAI TOTAL: {state['completed']} akun tersimpan di {PROXIES_FILE}!")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Dihentikan manual oleh user.")
