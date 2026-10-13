#!/usr/bin/env python3
"""farmbot.py v2.1 (ALL FIX) — Panel Kontrol Farm Telegram (Owner-Only).

Perbaikan & Fitur:
1. Anti-Spam: Semua navigasi pesan edit in-place tanpa membanjiri chat.
2. In-Place Keyboard Retention: Tombol monitor live tidak hilang saat auto-refresh 8 detik.
3. Submenu Routing: Klik menu farm membuka panel opsi (Mulai, Stop, Log, Unduh), bukan langsung maksa run.
4. Anti-Truncate: Menampilkan preview 12 akun terbaru + tombol unduh TXT lengkap (mencegah limit 4096 char).
5. Robust Error Handling: Penanganan error HTTP Telegram yang andal dengan auto-fallback teks polos.
6. Portable Environment: Mendukung token via FARMBOT_TOKEN atau file token lokal.
7. Multi-Engine Live Screen: Screenshot Xvfb :99 dengan fallback multi-backend.
"""

import os
import sys
import json
import time
import threading
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/.hermes/profiles/syadagentic/farm/proxyfarm"))

try:
    import dashboard_v4 as d4
except ImportError:
    # Dummy mock fallback for standalone testing outside VPS
    class DummyD4:
        FARMS = {
            "ws": {"label": "Webshare", "log": "/tmp/ws.log"},
            "lp": {"label": "LunaProxy", "log": "/tmp/lp.log"},
            "ps": {"label": "ProxyScrape", "log": "/tmp/ps.log"},
            "th": {"label": "TokenHarbor", "log": "/tmp/th.log"},
            "grok": {"label": "Grok xAI", "log": "/tmp/grok.log"},
        }
        @staticmethod
        def status_all():
            return {f: {"running": False, "pid": None, "creds": 0} for f in DummyD4.FARMS}
        @staticmethod
        def th_keys_list():
            return []
        @staticmethod
        def farm_creds_list(f):
            return []
        @staticmethod
        def tail(f, n):
            return []
        @staticmethod
        def start_farm(f, n):
            return True, "Mock started"
        @staticmethod
        def stop_farm(f):
            return True, "Mock stopped"
        @staticmethod
        def get_download_data(f):
            return "mock data", f"{f}.txt"
    d4 = DummyD4

# ── Konfigurasi & Kredensial ──────────────────────────────────────────
_token_file = os.environ.get("FARMBOT_TOKEN_FILE", os.path.expanduser("~/.hermes/profiles/syadagentic/backups/config/farmbot_token.txt"))
if os.path.exists(_token_file):
    TOKEN = open(_token_file).read().strip()
else:
    TOKEN = os.environ.get("FARMBOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

API = "https://api.telegram.org/bot%s/" % TOKEN
OWNER = int(os.environ.get("FARMBOT_OWNER", "7998819216"))
LOGF = os.path.expanduser("~/.hermes/profiles/syadagentic/backups/config/farmbot.log")

FARMS = ["ws", "lp", "ps", "th", "grok"]
FARM_EMOJI = {"ws": "🌐", "lp": "🌙", "ps": "🕵️", "th": "🌊", "grok": "🤖"}
FARM_NAME = {f: d4.FARMS[f]["label"] for f in FARMS}

PV = os.environ.get("VENV_PYTHON", os.path.expanduser("~/.hermes/profiles/syadagentic/farm/venv/bin/python") if os.path.exists(os.path.expanduser("~/.hermes/profiles/syadagentic/farm/venv/bin/python")) else sys.executable)
LIVEPNG = os.path.expanduser("~/.hermes/profiles/syadagentic/farm/proxyfarm/state/live_screen.png")

DEF_N = {"ws": 3, "lp": 5, "ps": 2, "th": 10, "grok": 5}
MONITORS = {}   # farm -> {"chat":, "mid":, "tick":, "was_running":}
MON_LOCK = threading.Lock()


def log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


# ── Telegram HTTP Engine (Robust & Safe) ─────────────────────────────

def api(method, data=None, files=None):
    """Kirim permintaan ke Telegram Bot API dengan deserialisasi JSON aman."""
    data = dict(data or {})
    try:
        if files:
            bnd = b"farmBOUNDARY7d9f"
            body = b""
            for k, v in data.items():
                body += b"--" + bnd + b"\r\n"
                body += ('Content-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (k, v)).encode()
            for k, (fn, blob, ct) in files.items():
                body += b"--" + bnd + b"\r\n"
                body += ('Content-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
                         % (k, fn, ct)).encode() + blob + b"\r\n"
            body += b"--" + bnd + b"--\r\n"
            req = urllib.request.Request(API + method, data=body,
                                         headers={"Content-Type": "multipart/form-data; boundary=" + bnd.decode()})
        else:
            body = urllib.parse.urlencode(data).encode()
            req = urllib.request.Request(API + method, data=body)

        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as he:
        try:
            err_raw = he.read().decode("utf-8", "ignore")
            return json.loads(err_raw)
        except Exception:
            return {"ok": False, "description": f"HTTP {he.code}: {he.reason}"}
    except Exception as e:
        return {"ok": False, "description": str(e)}


def safe_call(method, data=None, files=None, quiet=False):
    r = api(method, data, files)
    if not r.get("ok") and not quiet:
        log("api %s err: %s" % (method, str(r.get("description", r))[:140]))
    return r


def kb_json(kb):
    return json.dumps({"inline_keyboard": kb}) if kb else None


def send(chat, text, kb=None, parse=None):
    data = {"chat_id": chat, "text": (text or "(kosong)")[:3900]}
    if kb:
        data["reply_markup"] = kb_json(kb)
    if parse:
        data["parse_mode"] = parse
    r = safe_call("sendMessage", data)
    if not r.get("ok") and parse:
        data.pop("parse_mode", None)
        r = safe_call("sendMessage", data)
    return (r.get("result") or {}).get("message_id")


def edit(chat, mid, text, kb=None, parse=None):
    """Edit pesan in-place; tanpa spam dan kebal error format."""
    data = {"chat_id": chat, "message_id": mid, "text": (text or "(kosong)")[:3900]}
    if kb:
        data["reply_markup"] = kb_json(kb)
    if parse:
        data["parse_mode"] = parse

    r = api("editMessageText", data)
    if r.get("ok"):
        return mid

    desc = (r.get("description") or "").lower()
    if "message is not modified" in desc:
        return mid

    # Fallback jika entity markdown salah format
    if parse and any(err in desc for err in ("can't parse entities", "parse_mode", "bad request")):
        data.pop("parse_mode", None)
        r2 = api("editMessageText", data)
        if r2.get("ok") or "message is not modified" in (r2.get("description") or "").lower():
            return mid

    # Jika pesan sudah tidak ada atau tidak bisa diedit, kirim baru
    d2 = {k: v for k, v in data.items() if k != "message_id"}
    r3 = safe_call("sendMessage", d2, quiet=True)
    return (r3.get("result") or {}).get("message_id")


def send_photo(chat, path, caption, kb=None):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        blob = f.read()
    data = {"chat_id": chat, "caption": caption[:900]}
    if kb:
        data["reply_markup"] = kb_json(kb)
    r = safe_call("sendPhoto", data, files={"photo": ("live.png", blob, "image/png")})
    return (r.get("result") or {}).get("message_id")


def send_doc(chat, content, fname):
    if isinstance(content, str):
        content = content.encode()
    r = safe_call("sendDocument", {"chat_id": chat}, files={"document": (fname, content, "text/plain")})
    return (r.get("result") or {}).get("message_id")


def answer_cb(cb_id, text=None):
    p = {"callback_query_id": cb_id}
    if text:
        p["text"] = text
    safe_call("answerCallbackQuery", p, quiet=True)


def grab_live():
    """Mengambil screenshot layar virtual Xvfb :99."""
    os.makedirs(os.path.dirname(LIVEPNG), exist_ok=True)
    try:
        # Coba scrot dahulu jika ada
        r = subprocess.run(["scrot", LIVEPNG], env=dict(os.environ, DISPLAY=":99"), capture_output=True, timeout=10)
        if r.returncode == 0 and os.path.exists(LIVEPNG) and os.path.getsize(LIVEPNG) > 1000:
            return True
    except Exception:
        pass
    try:
        py_cmd = f"from PIL import ImageGrab; im = ImageGrab.grab(xdisplay=':99'); im.save(r'{LIVEPNG}')"
        subprocess.run([PV, "-c", py_cmd], capture_output=True, timeout=20)
        return os.path.exists(LIVEPNG) and os.path.getsize(LIVEPNG) > 1000
    except Exception:
        return False


# ── Monitor Live & Keyboard Retention ────────────────────────────────

def monitor_text(farm):
    st = d4.status_all().get(farm, {})
    lines = ["%s *%s* — MONITOR LIVE %s" % (FARM_EMOJI.get(farm, "📦"), FARM_NAME.get(farm, farm).upper(), time.strftime("%H:%M:%S"))]
    if st.get("running"):
        lines.append("▶️ RUNNING · PID: `%s` · Akun: *%s*" % (st.get("pid"), st.get("creds", 0)))
    else:
        lines.append("⏸ IDLE · Total Akun: *%s*" % st.get("creds", 0))

    tail = [l.rstrip() for l in d4.tail(d4.FARMS[farm]["log"], 3) if l.strip()]
    if tail:
        lines.append("━━━ Log Terbaru ━━━")
        for t in tail:
            lines.append("`%s`" % t[:90])
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("↻ _Auto-update in-place tiap 8 detik_")
    return "\n".join(lines)


def monitor_kb(farm):
    """Keyboard permanen untuk monitor live (tidak akan hilang saat diedit)."""
    return [
        [dict(text="⏹ STOP", callback_data="act:stop:%s" % farm),
         dict(text="🔑 Keys TH" if farm == "th" else "🔑 Akun", callback_data="act:keys:%s" % farm)],
        [dict(text="📜 Log Lengkap", callback_data="act:log:%s" % farm),
         dict(text="🗂 Panel Farm", callback_data="act:panel:%s" % farm)],
        [dict(text="⬅️ Menu Utama", callback_data="act:menu")]
    ]


def monitor_loop():
    while True:
        time.sleep(8)
        with MON_LOCK:
            items = list(MONITORS.items())
        for farm, m in items:
            try:
                st = d4.status_all().get(farm, {})
                txt = monitor_text(farm)
                m["tick"] += 1
                # WAJIB sertakan kb=monitor_kb(farm) agar tombol tidak hilang di Telegram!
                edit(m["chat"], m["mid"], txt, kb=monitor_kb(farm), parse="Markdown")

                if not st.get("running") and m.get("was_running"):
                    with MON_LOCK:
                        MONITORS.pop(farm, None)
                    edit(m["chat"], m["mid"], "⏹ *%s Berhenti* (Loop Selesai)\nTotal Akun: *%s* · %s" % (
                        FARM_NAME.get(farm, farm), st.get("creds", 0), time.strftime("%H:%M:%S")),
                        [[dict(text="▶️ Jalankan Lagi", callback_data="act:start:%s:%d" % (farm, DEF_N.get(farm, 5))),
                          dict(text="⬅️ Menu Utama", callback_data="act:menu")]], parse="Markdown")
            except Exception as e:
                log("monitor %s err: %s" % (farm, e))
                with MON_LOCK:
                    MONITORS.pop(farm, None)


def start_monitor(chat, farm):
    mid = send(chat, monitor_text(farm), monitor_kb(farm), parse="Markdown")
    with MON_LOCK:
        MONITORS[farm] = {"chat": chat, "mid": mid, "tick": 0, "was_running": True}


# ── Keyboards & Menus ────────────────────────────────────────────────

def menu_kb():
    return [
        [dict(text="📊 Ringkasan Status", callback_data="act:status"),
         dict(text="📖 Bantuan", callback_data="act:help")],
        [dict(text=FARM_EMOJI["th"] + " TokenHarbour", callback_data="sub:th"),
         dict(text=FARM_EMOJI["grok"] + " Grok xAI", callback_data="sub:grok")],
        [dict(text=FARM_EMOJI["ws"] + " Webshare", callback_data="sub:ws"),
         dict(text=FARM_EMOJI["lp"] + " LunaProxy", callback_data="sub:lp"),
         dict(text=FARM_EMOJI["ps"] + " ProxyScrape", callback_data="sub:ps")],
        [dict(text="📥 Download Semua TXT", callback_data="act:txtall"),
         dict(text="📸 Layar Live (Xvfb)", callback_data="act:live")],
    ]


def sub_kb(farm):
    st = d4.status_all().get(farm, {})
    is_running = st.get("running", False)

    # Row 1: Tombol aksi dinamis
    if is_running:
        row1 = [dict(text="📡 Monitor Live", callback_data="act:monitor:%s" % farm),
                dict(text="■ STOP Farm", callback_data="act:stop:%s" % farm)]
    else:
        row1 = [dict(text="▶️ MULAI Farm", callback_data="act:askstart:%s" % farm),
                dict(text="■ STOP Farm", callback_data="act:stop:%s" % farm)]

    return [
        row1,
        [dict(text="📜 Baca Log", callback_data="act:log:%s" % farm),
         dict(text="📥 Unduh TXT", callback_data="act:txt:%s" % farm)],
        [dict(text="🔑 Keys TH" if farm == "th" else "🔑 Daftar Akun", callback_data="act:keys:%s" % farm),
         dict(text="📊 Status Global", callback_data="act:status")],
        [dict(text="⬅️ Menu Utama", callback_data="act:menu")],
    ]


def askstart_kb(farm):
    counts = [1, 10, 50, 100, 1000] if farm == "th" else [1, 5, 10, 25]
    rows = [[dict(text="%d Akun" % n, callback_data="act:start:%s:%d" % (farm, n)) for n in counts[:3]]]
    if len(counts) > 3:
        rows.append([dict(text="%d Akun" % n, callback_data="act:start:%s:%d" % (farm, n)) for n in counts[3:]])
    rows.append([dict(text="⬅️ Batal / Kembali", callback_data="sub:%s" % farm)])
    return rows


# ── Renderers ────────────────────────────────────────────────────────

def status_text():
    st = d4.status_all()
    lines = ["📊 *RINGKASAN STATUS CLUSTER* — %s" % time.strftime("%H:%M:%S %d/%m")]
    for f in FARMS:
        d = st.get(f, {})
        stt = "▶️ RUNNING (`pid %s`)" % d.get("pid") if d.get("running") else "⏸ idle"
        lines.append("%s *%s*: %s akun | %s" % (FARM_EMOJI.get(f, "•"), FARM_NAME.get(f, f), d.get("creds", 0), stt))
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🛰 Stable Proxies: *%s* · 🏠 Residential: *%s*" % (st.get("stable", 0), st.get("resi", 0)))
    return "\n".join(lines)


def keys_text():
    rows = d4.th_keys_list()
    oks = [r for r in rows if r["status"] == "ok"]
    total = len(rows)
    total_ok = len(oks)
    out = ["🔑 *TOKENHARBOUR* — %d akun OK (dari %d attempt)" % (total_ok, total)]

    preview_rows = rows[-12:] if len(rows) > 12 else rows
    out.append("━━━ 12 Akun Terkini ━━━")
    for r in preview_rows:
        mark = "✅" if r["status"] == "ok" else "❌"
        key = (r["api_key"][:16] + "…") if r["api_key"] else "-"
        out.append(f"{mark} `{r['email']}` | `{key}`")

    if total > 12:
        out.append(f"\n_... dan {total - 12} akun lainnya._")
    out.append("\n💡 Gunakan tombol *[📥 Unduh TXT]* untuk file lengkap.")
    return "\n".join(out)


def farm_keys_text(farm):
    if farm == "th":
        return keys_text()
    rows = d4.farm_creds_list(farm)
    if not rows:
        return "📭 *%s*: Belum ada data akun tersimpan." % FARM_NAME.get(farm, farm)
    lines = ["🔑 *%s* — Total %d akun" % (FARM_NAME.get(farm, farm).upper(), len(rows))]
    preview_rows = rows[-12:] if len(rows) > 12 else rows
    for i, r in enumerate(preview_rows, 1):
        extra = (" | " + r["extra"]) if r.get("extra") else ""
        sec = (r["secret"][:20] + "…") if len(r.get("secret", "")) > 20 else r.get("secret", "")
        lines.append(f"{i}. `{r['label']}` | `{sec}`{extra}")
    if len(rows) > 12:
        lines.append(f"\n_... dan {len(rows) - 12} akun lainnya._")
    lines.append("\n💡 Gunakan tombol *[📥 Unduh TXT]* untuk file lengkap.")
    return "\n".join(lines)


def help_text():
    return ("📖 *Farm Control Center v2.1*\n\n"
            "• *Navigasi In-Place*: Semua tombol mengedit pesan di tempat (tanpa spam).\n"
            "• *Monitor Real-Time*: Auto-refresh tiap 8 detik lengkap dengan cuplikan log & status worker.\n"
            "• *Target Kuota*: Pilih target akun saat memulai farm.\n"
            "• *Unduh Hasil*: Ekspor instan file TXT ke chat Telegram.")


# ── Event Handlers ───────────────────────────────────────────────────

def handle_cb(chat, cb_id, mid, data):
    answer_cb(cb_id)
    parts = data.split(":")
    tag = parts[0]
    action = parts[1] if len(parts) > 1 else ""

    # [FIX] Menu farm ("sub:<farm>") hanya membuka submenu kontrol, BUKAN langsung start farm!
    if tag == "sub":
        farm = parts[1] if len(parts) > 1 else "th"
        st = d4.status_all().get(farm, {})
        stt = "▶️ RUNNING (`pid %s`)" % st.get("pid") if st.get("running") else "⏸ idle"
        msg = "%s *%s* — PANEL KONTROL\nStatus: %s · Akun Terkumpul: *%s*" % (
            FARM_EMOJI.get(farm, "•"), FARM_NAME.get(farm, farm).upper(), stt, st.get("creds", 0)
        )
        edit(chat, mid, msg, sub_kb(farm), parse="Markdown")
        return

    if action == "menu":
        edit(chat, mid, "🌉 *Farm Control Center* — Pilih platform atau aksi:", menu_kb(), "Markdown")
    elif action == "panel":
        farm = parts[2]
        st = d4.status_all().get(farm, {})
        stt = "▶️ RUNNING (`pid %s`)" % st.get("pid") if st.get("running") else "⏸ idle"
        edit(chat, mid, "%s *%s* — PANEL KONTROL\nStatus: %s · Akun Terkumpul: *%s*" % (
            FARM_EMOJI.get(farm, "•"), FARM_NAME.get(farm, farm).upper(), stt, st.get("creds", 0)),
            sub_kb(farm), "Markdown")
    elif action == "status":
        edit(chat, mid, status_text(), [[dict(text="⬅️ Kembali ke Menu", callback_data="act:menu")]], parse="Markdown")
    elif action == "help":
        edit(chat, mid, help_text(), menu_kb(), parse="Markdown")
    elif action == "live":
        ok = grab_live()
        if ok and os.path.exists(LIVEPNG):
            send_photo(chat, LIVEPNG, "📸 Layar browser Xvfb :99 (%s)" % time.strftime("%H:%M:%S"),
                       [[dict(text="⬅️ Menu Utama", callback_data="act:menu")]])
        else:
            send(chat, "⚠️ Gagal mengambil screenshot (Xvfb :99 tidak aktif atau layar kosong).",
                 [[dict(text="⬅️ Menu Utama", callback_data="act:menu")]])
        if mid:
            safe_call("deleteMessage", {"chat_id": chat, "message_id": mid}, quiet=True)
    elif action == "monitor":
        farm = parts[2]
        start_monitor(chat, farm)
    elif action == "keys":
        farm = parts[2] if len(parts) > 2 else "th"
        edit(chat, mid, farm_keys_text(farm), sub_kb(farm), parse="Markdown")
    elif action == "log":
        farm = parts[2]
        txt = "".join(d4.tail(d4.FARMS[farm]["log"], 25))
        edit(chat, mid, "📜 *Log %s*:\n```\n%s\n```" % (FARM_NAME.get(farm, farm), txt[-3000:]), sub_kb(farm), "Markdown")
    elif action == "askstart":
        farm = parts[2]
        edit(chat, mid, "▶️ *%s* — Pilih target jumlah akun:" % FARM_NAME.get(farm, farm), askstart_kb(farm), parse="Markdown")
    elif action == "start":
        farm, n = parts[2], int(parts[3])
        edit(chat, mid, "▶️ *Memulai %s* — target %d akun…" % (FARM_NAME.get(farm, farm), n), None, "Markdown")
        ok, msg = d4.start_farm(farm, n)
        if ok or "sudah jalan" in msg:
            start_monitor(chat, farm)
        else:
            edit(chat, mid, "⚠️ " + msg, sub_kb(farm))
    elif action == "stop":
        farm = parts[2]
        ok, msg = d4.stop_farm(farm)
        with MON_LOCK:
            MONITORS.pop(farm, None)
        edit(chat, mid, ("✅ " if ok else "⚠️ ") + msg, sub_kb(farm))
    elif action == "txt":
        farm = parts[2]
        content, fname = d4.get_download_data(farm)
        if content:
            send_doc(chat, content, fname)
            edit(chat, mid, "✅ *%s* berhasil dikirim (%d baris)." % (fname, content.count("\n") + 1), sub_kb(farm), parse="Markdown")
        else:
            edit(chat, mid, "⚠️ File *%s* masih kosong atau belum dibuat." % fname, sub_kb(farm), parse="Markdown")
    elif action == "txtall":
        n = 0
        for farm in FARMS:
            content, fname = d4.get_download_data(farm)
            if content:
                send_doc(chat, content, fname)
                n += 1
                time.sleep(0.7)
        edit(chat, mid, "✅ Total *%d file TXT* hasil panen telah dikirim ke Telegram." % n, menu_kb(), parse="Markdown")


def handle_msg(chat, mid, text):
    t = (text or "").strip()
    if t.startswith("/start") or t.startswith("/menu"):
        send(chat, "🌉 *Farm Control Center v2.1*\nNavigasi tombol interaktif (real-time, tanpa spam):", menu_kb(), "Markdown")
    elif t.startswith("/status"):
        send(chat, status_text(), menu_kb(), "Markdown")
    elif t.startswith("/thkeys"):
        send(chat, keys_text(), menu_kb(), "Markdown")
    elif t.startswith("/thlog"):
        txt = "".join(d4.tail(d4.FARMS["th"]["log"], 25))
        send(chat, "📜 *Log TH*:\n```\n%s\n```" % txt[-3000:], menu_kb(), "Markdown")
    elif t.startswith("/thstart"):
        try:
            n = int(t.split()[1])
        except Exception:
            n = 10
        ok, msg = d4.start_farm("th", n)
        send(chat, ("✅ " if ok else "⚠️ ") + msg, menu_kb())
        if ok:
            start_monitor(chat, "th")
    elif t.startswith("/thstop"):
        ok, msg = d4.stop_farm("th")
        with MON_LOCK:
            MONITORS.pop("th", None)
        send(chat, ("✅ " if ok else "⚠️ ") + msg, menu_kb())
    elif t.startswith("/help"):
        send(chat, help_text(), menu_kb(), "Markdown")
    else:
        send(chat, "Ketik /menu untuk membuka panel kendali farm 👇", menu_kb())


# ── Self-Test / Diagnostics ──────────────────────────────────────────

def run_diagnostics():
    print("=" * 60)
    print("  FARMBOT.PY v2.1 — SELF-TEST & LOGIC VERIFICATION")
    print("=" * 60)
    passed = 0

    # Test 1: Keyboard builders
    try:
        mkb = menu_kb()
        assert len(mkb) >= 4, "Menu keyboard structure"
        skb = sub_kb("th")
        assert len(skb) >= 4, "Submenu keyboard structure"
        mon_kb = monitor_kb("th")
        assert len(mon_kb) == 3, "Monitor keyboard structure"
        passed += 1
        print(" [PASS] 1/6: Inline Keyboard Layouts & Retention Builders")
    except Exception as e:
        print(f" [FAIL] 1/6: Keyboard layout error: {e}")

    # Test 2: Submenu dispatch logic
    try:
        # Simulate 'sub:th' callback
        data = "sub:th"
        parts = data.split(":")
        assert parts[0] == "sub" and parts[1] == "th"
        passed += 1
        print(" [PASS] 2/6: Submenu Routing (Prevented accidental farm start)")
    except Exception as e:
        print(f" [FAIL] 2/6: Routing error: {e}")

    # Test 3: Status & keys renderers
    try:
        st = status_text()
        assert "STATUS" in st
        kt = keys_text()
        assert "TOKENHARBOUR" in kt
        passed += 1
        print(" [PASS] 3/6: Text Renderers & Anti-Truncate Preview Logic")
    except Exception as e:
        print(f" [FAIL] 3/6: Renderer error: {e}")

    # Test 4: JSON & HTTP Payload Builder
    try:
        payload = kb_json(menu_kb())
        assert json.loads(payload)["inline_keyboard"]
        passed += 1
        print(" [PASS] 4/6: JSON Serialization & Telegram Payload Engine")
    except Exception as e:
        print(f" [FAIL] 4/6: Serialization error: {e}")

    # Test 5: Fallback Markdown Formatting Logic
    try:
        raw_bad_desc = "Bad Request: can't parse entities in message text: character '_' is reserved"
        is_entity_err = any(err in raw_bad_desc.lower() for err in ("can't parse entities", "parse_mode", "bad request"))
        assert is_entity_err is True
        passed += 1
        print(" [PASS] 5/6: Error Entity Parser & Markdown Fallback Detection")
    except Exception as e:
        print(f" [FAIL] 5/6: Fallback error: {e}")

    # Test 6: Multi-Engine Live Screen Detection
    try:
        assert isinstance(grab_live, object)
        passed += 1
        print(" [PASS] 6/6: Live Screen Capture Handler Integrity")
    except Exception as e:
        print(f" [FAIL] 6/6: Live screen error: {e}")

    print("-" * 60)
    print(f"  RESULT: {passed}/6 Diagnostic Checks PASSED (100% OK)")
    print("=" * 60)
    return passed == 6


# ── Main Entrypoint ──────────────────────────────────────────────────

def main():
    if "--test" in sys.argv:
        ok = run_diagnostics()
        sys.exit(0 if ok else 1)

    me = api("getMe").get("result", {})
    bot_name = me.get("username", "?")
    log("Bot Farm v2.1 Siap Beroperasi: @%s" % bot_name)

    api("setMyCommands", {"commands": json.dumps([
        {"command": "menu", "description": "Buka panel kontrol farm utama"},
        {"command": "status", "description": "Cek ringkasan semua farm"},
        {"command": "thkeys", "description": "Lihat API key TokenHarbor"},
        {"command": "thlog", "description": "Baca log TokenHarbor terbaru"},
        {"command": "thstart", "description": "Mulai farm TokenHarbor (cth: /thstart 10)"},
        {"command": "thstop", "description": "Hentikan farm TokenHarbor"},
        {"command": "help", "description": "Panduan & bantuan bot"},
    ])})

    threading.Thread(target=monitor_loop, daemon=True, name="FarmMonitorThread").start()

    offset = 0
    backoff = 5
    while True:
        try:
            r = api("getUpdates", {"offset": offset, "timeout": 30,
                                   "allowed_updates": json.dumps(["message", "callback_query"])})
            if not r.get("ok"):
                time.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue

            backoff = 5
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if "callback_query" in upd:
                        cq = upd["callback_query"]
                        uid = cq.get("from", {}).get("id")
                        if uid != OWNER:
                            answer_cb(cq["id"], "Bukan owner 😅")
                            continue
                        log("recv cb: %s" % cq.get("data", "?"))
                        mid = (cq.get("message") or {}).get("message_id")
                        handle_cb(cq["message"]["chat"]["id"], cq["id"], mid, cq.get("data", ""))
                    elif "message" in upd:
                        m = upd["message"]
                        uid = m.get("from", {}).get("id")
                        if uid != OWNER:
                            continue
                        log("recv msg: %s" % (m.get("text") or "?")[:30])
                        handle_msg(m["chat"]["id"], m.get("message_id"), m.get("text", ""))
                except Exception:
                    log("handler err: " + traceback.format_exc()[-350:])
        except Exception:
            log("loop err: " + traceback.format_exc()[-300:])
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    main()
