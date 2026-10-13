#!/usr/bin/env python3
"""dashboard_v4.py — farm control center.

Halaman: Farm Webshare | Farm Chutes | Farm LunaProxy | Farm ProxyScrape
         | Farm TokenHarbour | Pool & Engine (view) | Hasil Farm
API (token auth via ?token=):
  GET  /                      -> farms_v4.html
  GET  /api/status            -> overview semua farm
  POST /api/farm/<name>/start {count}   name: ws|chutes|lp|ps|th
  POST /api/farm/<name>/stop
  GET  /api/farm/<name>/log?lines=60
  GET  /api/pool              -> pool.json + pool_hist + stable/resi counts
  GET  /api/creds             -> jumlah + contoh (masked) creds per provider
Token: state/dash_token.txt (regenerate kalau hilang) — print ke stdout saat boot.

Rebuilt 2026-10-10 v4.1 (Fix real cred counts & paths across all 5 providers).
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCH = os.path.dirname(HERE)
STATE = os.path.join(HERE, "state")
TOOLS = os.path.join(SCRATCH, "captcha-tools")
VENV_PY = os.path.join(SCRATCH, "venv", "bin", "python")
PORT = 8091

os.makedirs(STATE, exist_ok=True)
TPATH = os.path.join(STATE, "dash_token.txt")
if os.path.exists(TPATH):
    TOKEN = open(TPATH).read().strip()
else:
    import secrets as _s
    TOKEN = _s.token_urlsafe(18)
    open(TPATH, "w").write(TOKEN)

# token lama (bookmark user) tetap diterima
LEGACY = {"eBSEI0LPZOE6-AV8Q6TyhQ"}

FARMS = {
    "ws": {
        "label": "Webshare",
        "cmd": lambda n: ["bash", "-lc", "while :; do %s %s --accounts %d; sleep 5; done" % (
            VENV_PY, os.path.join(TOOLS, "webshare_farm.py"), n)],
        "log": os.path.join(STATE, "wsfarm.log"),
        "creds": os.path.join(TOOLS, "ws_creds_archive"),
        "type": "dir_count",
        "cwd": TOOLS,
        "kill_pattern": "webshare_farm\.py",
    },
    "lp": {
        "label": "LunaProxy",
        "cmd": lambda n: ["bash", "-lc", "exec %s %s --accounts %d" % (
            VENV_PY, os.path.join(TOOLS, "lunaproxy_farm.py"), n)],
        "log": os.path.join(STATE, "lpfarm.log"),
        "creds": os.path.join(TOOLS, "lunaproxy_creds.jsonl"),
        "type": "jsonl_count",
        "cwd": TOOLS,
        "kill_pattern": "lunaproxy_farm\.py",
    },
    "ps": {
        "label": "ProxyScrape",
        "cmd": lambda n: ["bash", "-lc", (
            f"cd {os.path.join(SCRATCH, 'turnstile-solver-x')} && while :; do "
            f"echo \"== ps loop ==\"; "
            f"{VENV_PY} ps_signup_v2.py; "
            f"sleep 5; done")],
        "log": os.path.join(STATE, "psfarm.log"),
        "creds": os.path.join(SCRATCH, "turnstile-solver-x", "ps_creds_v2.json"),
        "type": "json_count",
        "cwd": os.path.join(SCRATCH, "turnstile-solver-x"),
        "kill_pattern": "ps_signup_v2\.py|solve_turnstile\.py https://dashboard\.proxyscrape\.com",
    },
    "th": {
        "label": "TokenHarbour",
        "cmd": lambda n: [VENV_PY, os.path.join(SCRATCH, "tokenharbour-farm", "th_harvest.py"),
                          "--target", str(n), "--batch", "4", "--max-rounds", "9999"],
        "log": os.path.join(STATE, "thfarm.log"),
        "creds": os.path.join(SCRATCH, "tokenharbour-farm", "token-harbor-main", "tokenharbor_keys.txt"),
        "type": "ok_lines",
        "cwd": os.path.join(SCRATCH, "tokenharbour-farm"),
        "kill_pattern": "th_harvest\.py|solve_turnstile\.py https://tokenharbor\.ai",
    },
    "grok": {
        "label": "Grok xAI",
        "cmd": lambda n: ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", "root@100.101.183.120",
                          f"cd /root/syadagentic/farm/grok-tools/v3/grok-farm && exec env DISPLAY=:99 PYTHONUNBUFFERED=1 /root/syadagentic/farm/venv/bin/python -u grok.py {n} --no-install --proxy 'http://ra6rkh60jg6q:il03a2w445yh2z5@104.207.53.249:3129'"],
        "log": os.path.join(STATE, "grokfarm.log"),
        "creds": os.path.join(SCRATCH, "grok-tools", "v3", "grok-farm", "sso.txt"),
        "type": "jsonl_count",
        "cwd": os.path.join(SCRATCH, "grok-tools", "v3", "grok-farm"),
        "kill_pattern": "grok\.py",
    },
}


def alive(pid):
    try:
        os.kill(pid, 0)
    except Exception:
        return False
    try:
        with open("/proc/%d/status" % pid) as f:
            for line in f:
                if line.startswith("State:"):
                    if "Z" in line or "T" in line:
                        return False
                    break
    except Exception:
        pass
    return True


def farm_pid(name):
    p = os.path.join(STATE, name + ".pid")
    if os.path.exists(p):
        try:
            pid = int(open(p).read().strip())
            if alive(pid):
                return pid
            try:
                os.remove(p)
            except Exception:
                pass
        except Exception:
            pass
    return None


def start_farm(name, count):
    f = FARMS[name]
    if farm_pid(name):
        return False, "%s sudah jalan (pid %d)" % (f["label"], farm_pid(name))
    # Bunuh orphan dari start sebelumnya biar ga numpuk
    _kill_orphans(name)
    log = open(f["log"], "a")
    log.write("\n===== dashboard start %s accounts=%d =====\n" % (
        time.strftime("%H:%M:%S"), count))
    log.flush()
    workdir = f.get("cwd", TOOLS)
    proc = subprocess.Popen(f["cmd"](count), stdout=log, stderr=log,
                            cwd=workdir, start_new_session=True)
    open(os.path.join(STATE, name + ".pid"), "w").write(str(proc.pid))
    return True, "%s farm start pid=%d (%d akun)" % (f["label"], proc.pid, count)


def _kill_orphans(name):
    """Bunuh semua proses kill_pattern milik farm ini (root cause 'stop tapi status ON lagi')."""
    pat = FARMS[name].get("kill_pattern")
    if not pat:
        return 0
    try:
        out = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True).stdout
        n = 0
        for pid_s in out.split():
            try:
                pid = int(pid_s)
                if pid == os.getpid():
                    continue
                os.kill(pid, signal.SIGKILL)
                n += 1
            except Exception:
                pass
        return n
    except Exception:
        return 0


def stop_farm(name):
    pid = farm_pid(name)
    p = os.path.join(STATE, name + ".pid")
    if not pid:
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass
        try:
            os.remove(p)
        except Exception:
            pass
    orph = _kill_orphans(name)
    return True, "%s stop (pid %s, %d child + %d orphan dimatikan)" % (
        FARMS[name]["label"], pid or "-", 0, orph)


def tail(path, lines=60):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", errors="replace") as f:
            return f.readlines()[-lines:]
    except Exception:
        return []


def count_lines(path):
    try:
        return sum(1 for l in open(path, errors="replace")
                   if l.strip() and not l.startswith("#"))
    except Exception:
        return 0


def count_creds(f):
    cpath = f["creds"]
    ctype = f.get("type")
    if not os.path.exists(cpath):
        return 0
    if ctype == "dir_count":
        return len([x for x in os.listdir(cpath) if x.endswith(".json")])
    elif ctype == "jsonl_count":
        return sum(1 for l in open(cpath, errors="replace") if l.strip())
    elif ctype == "json_count":
        try:
            return len(json.load(open(cpath)))
        except Exception:
            return 0
    elif ctype == "ok_lines":
        return sum(1 for l in open(cpath, errors="replace") if "|ok" in l or l.strip().endswith("|ok"))
    return 0


def status_all():
    out = {}
    for k, f in FARMS.items():
        pid = farm_pid(k)
        n = count_creds(f)
        out[k] = {"label": f["label"], "running": bool(pid), "pid": pid,
                  "creds": n, "log_tail": "".join(tail(f["log"], 6))[-600:]}
    stable = os.path.join(HERE, "stable_proxies.txt")
    resi = os.path.join(HERE, "residential_proxies.txt")
    out["stable"] = count_lines(stable)
    out["resi"] = count_lines(resi)
    return out


def pool_info():
    pf = HERE
    d = {}
    try:
        pj = json.load(open(os.path.join(pf, "pool.json")))
        d["counts"] = pj.get("counts", {})
        d["ts"] = pj.get("ts")
        d["direct_ip"] = pj.get("direct_ip")
        d["pool_total"] = len(pj.get("proxies", []))
    except Exception as e:
        d["pool_err"] = str(e)
    try:
        h = json.load(open(os.path.join(pf, "state", "pool_hist.json")))
        d["hist"] = h[-60:] if isinstance(h, list) else h
    except Exception as e:
        d["hist_err"] = str(e)
    d["stable"] = count_lines(os.path.join(pf, "stable_proxies.txt"))
    d["resi"] = count_lines(os.path.join(pf, "residential_proxies.txt"))
    return d


def creds_info():
    out = {}
    st = status_all()
    out["webshare"] = {"n": st["ws"]["creds"]}
    out["lunaproxy"] = {"n": st["lp"]["creds"]}
    out["proxyscrape"] = {"n": st["ps"]["creds"]}
    out["tokenharbour"] = {"n": st["th"]["creds"]}
    out["grok"] = {"n": st.get("grok", {}).get("creds", 0)}

    ex = {}
    # Webshare
    ws_dir = os.path.join(TOOLS, "ws_creds_archive")
    if os.path.exists(ws_dir):
        files = [x for x in os.listdir(ws_dir) if x.endswith(".json")]
        if files:
            try:
                r = json.load(open(os.path.join(ws_dir, files[-1])))
                ex["webshare"] = {"email": r.get("email", files[-1].replace(".json", "")),
                                  "status": r.get("status", "archived")}
            except Exception:
                pass
    # Grok xAI
    gk = os.path.join(SCRATCH, "grok-tools", "grok_creds.jsonl")
    if os.path.exists(gk):
        try:
            lines = [l for l in open(gk) if l.strip()]
            if lines:
                r = json.loads(lines[-1])
                ex["grok"] = {"email": r.get("email", ""), "access_token": (r.get("access_token") or "")[:12] + "..."}
        except Exception:
            pass
    # LunaProxy
    ll = os.path.join(TOOLS, "lunaproxy_creds.jsonl")
    if os.path.exists(ll):
        try:
            lines = [l for l in open(ll) if l.strip()]
            if lines:
                r = json.loads(lines[-1])
                ex["lp"] = {"account": r.get("account", ""), "email": r.get("email", "")}
        except Exception:
            pass
    # ProxyScrape
    ps = os.path.join(SCRATCH, "turnstile-solver-x", "ps_creds_v2.json")
    if os.path.exists(ps):
        try:
            d = json.load(open(ps))
            if d:
                r = d[-1]
                ex["proxyscrape"] = {"email": r.get("email", ""), "verified": r.get("verified", False)}
        except Exception:
            pass
    # TokenHarbour
    th = os.path.join(SCRATCH, "tokenharbour-farm", "token-harbor-main", "tokenharbor_keys.txt")
    if os.path.exists(th):
        try:
            oks = [l.strip() for l in open(th) if "|ok" in l or l.strip().endswith("|ok")]
            if oks:
                parts = oks[-1].split("|")
                ex["tokenharbour"] = {"email": parts[0], "key": parts[2][:12] + "..." if len(parts) > 2 else "YES"}
        except Exception:
            pass

    out["sample"] = ex
    return out


def th_keys_list():
    th = os.path.join(SCRATCH, "tokenharbour-farm", "token-harbor-main", "tokenharbor_keys.txt")
    if not os.path.exists(th):
        return []
    out = []
    for line in open(th, errors="replace"):
        l = line.strip()
        if not l or l.startswith("#"):
            continue
        parts = l.split("|")
        if len(parts) >= 4:
            out.append({
                "email": parts[0],
                "password": parts[1],
                "api_key": parts[2],
                "status": parts[3]
            })
    out.sort(key=lambda x: (x["status"] != "ok"))
    return out


def farm_creds_list(name):
    """Daftar akun per farm -> [{"label","secret","extra"}] — buat bot (paritas sm TH)."""
    import json as _json
    out = []
    try:
        if name == "th":
            for r in th_keys_list():
                out.append({"label": r["email"], "secret": r["api_key"] or "-", "extra": r["status"]})
            return out
        if name == "ws":
            d = os.path.join(TOOLS, "ws_creds_archive")
            for f in sorted(os.listdir(d)):
                if not f.endswith(".json"):
                    continue
                try:
                    j = _json.load(open(os.path.join(d, f), errors="replace"))
                except Exception:
                    continue
                out.append({"label": j.get("email", f), "secret": j.get("password", "-"), "extra": "session" if j.get("session_token") else ""})
        elif name == "grok":
            p = os.path.join(SCRATCH, "grok-tools", "v3", "grok-farm", "sso.txt")
            if os.path.exists(p):
                for line in open(p, errors="replace"):
                    if not line.strip():
                        continue
                    try:
                        j = _json.loads(line)
                    except Exception:
                        continue
                    out.append({"label": j.get("email", "?"), "secret": (j.get("sso") or j.get("password") or "-")[:20] + "...", "extra": "sso"})
        elif name == "lp":
            p = os.path.join(TOOLS, "lunaproxy_creds.jsonl")
            if os.path.exists(p):
                for line in open(p, errors="replace"):
                    if not line.strip():
                        continue
                    try:
                        j = _json.loads(line)
                    except Exception:
                        continue
                    out.append({"label": j.get("email", "?"), "secret": j.get("password", "-"), "extra": j.get("account", "")})
        elif name == "ps":
            p = os.path.join(SCRATCH, "turnstile-solver-x", "ps_creds_v2.json")
            if os.path.exists(p):
                data = _json.load(open(p, errors="replace"))
                rows = data if isinstance(data, list) else [data]
                for j in rows:
                    if isinstance(j, dict):
                        out.append({"label": j.get("email", "?"), "secret": j.get("password", "-"), "extra": "tok" if j.get("access_token") else ""})
    except Exception as e:
        return [{"label": "⚠️ error", "secret": str(e), "extra": ""}]
    return out


def get_download_data(name):
    if name == "th":
        p = os.path.join(SCRATCH, "tokenharbour-farm", "token-harbor-main", "tokenharbor_keys.txt")
        return open(p, errors="replace").read() if os.path.exists(p) else "", "tokenharbor_keys.txt"
    elif name == "ws":
        p = os.path.join(TOOLS, "ws_farm_proxies.txt")
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            p = os.path.join(HERE, "stable_proxies.txt")
        return open(p, errors="replace").read() if os.path.exists(p) else "", "webshare_proxies.txt"
    elif name == "grok":
        # Farm grok pindah ke sandbox — tarik sso.txt dari sana, merge dgn lokal, dedupe.
        chunks = []
        try:
            out = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
                 "root@100.101.183.120",
                 "cat /root/syadagentic/farm/grok-tools/v3/grok-farm/sso.txt 2>/dev/null"],
                capture_output=True, text=True, timeout=15)
            if out.stdout and out.stdout.strip():
                chunks.append(out.stdout.strip())
        except Exception:
            pass
        for p in (os.path.join(SCRATCH, "grok-tools", "v3", "grok-farm", "sso.txt"),
                  os.path.join(SCRATCH, "grok-tools", "grok_creds.jsonl")):
            if os.path.exists(p):
                try:
                    chunks.append(open(p, errors="replace").read().strip())
                except Exception:
                    pass
        seen, rows = set(), []
        for line in "\n".join(chunks).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                em = json.loads(line).get("email", line)
            except Exception:
                em = line
            if em in seen:
                continue
            seen.add(em)
            rows.append(line)
        return ("\n".join(rows) + ("\n" if rows else ""), "grok_sso.txt")
    elif name == "lp":
        p = os.path.join(TOOLS, "lunaproxy_creds.jsonl")
        return open(p, errors="replace").read() if os.path.exists(p) else "", "lunaproxy_creds.txt"
    elif name == "ps":
        p = os.path.join(SCRATCH, "turnstile-solver-x", "ps_creds_v2.json")
        if os.path.exists(p):
            try:
                data = json.load(open(p))
                lines = [f"{x.get('email')}|{x.get('password')}|verified={x.get('verified')}" for x in data]
                return "\n".join(lines), "proxyscrape_creds.txt"
            except Exception:
                return open(p, errors="replace").read(), "proxyscrape_creds.json"
        return "", "proxyscrape_creds.txt"
    elif name == "stable":
        p = os.path.join(HERE, "stable_proxies.txt")
        return open(p, errors="replace").read() if os.path.exists(p) else "", "stable_proxies.txt"
    elif name == "resi":
        p = os.path.join(HERE, "residential_proxies.txt")
        return open(p, errors="replace").read() if os.path.exists(p) else "", "residential_proxies.txt"
    return "", "download.txt"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _auth(self, q):
        t = urllib.parse.parse_qs(q).get("token", [""])[0]
        return t == TOKEN or t in LEGACY

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = u.query
        # format lama: /t/<token> -> redirect ke /?token=<token>
        if u.path.startswith("/t/"):
            tok = u.path.split("/")[2] if len(u.path.split("/")) > 2 else ""
            b = ("/?token=" + urllib.parse.quote(tok)).encode()
            self.send_response(302)
            self.send_header("Location", b.decode())
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if u.path == "/":
            html_file = os.path.join(HERE, "farms_v4.html")
            if os.path.exists(html_file):
                b = open(html_file, encoding="utf-8").read().encode()
            else:
                b = b"farms_v4.html belum ada"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        if not self._auth(q):
            self._json({"error": "unauthorized"}, 401)
            return
        elif u.path == "/api/status":
            self._json(status_all())
        elif u.path == "/api/pool":
            self._json(pool_info())
        elif u.path == "/api/creds":
            self._json(creds_info())
        elif u.path == "/api/farm/th/keys":
            self._json(th_keys_list())
        elif u.path.startswith("/api/farm/") and u.path.endswith("/download"):
            name = u.path.split("/")[3]
            content, fname = get_download_data(name)
            b = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        elif u.path.startswith("/api/farm/") and u.path.endswith("/log"):
            name = u.path.split("/")[3]
            if name not in FARMS:
                self._json({"error": "unknown farm"}, 404)
                return
            n = int(urllib.parse.parse_qs(q).get("lines", ["60"])[0])
            self._json({"log": "".join(tail(FARMS[name]["log"], n))})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if not self._auth(u.query):
            self._json({"error": "unauthorized"}, 401)
            return
        parts = u.path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "farm":
            name, action = parts[2], parts[3]
            if name not in FARMS:
                self._json({"error": "unknown farm"}, 404)
                return
            ln = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(ln) or b"{}")
            except Exception:
                body = {}
            if action == "start":
                ok, msg = start_farm(name, int(body.get("count", 1)))
                self._json({"ok": ok, "msg": msg})
            elif action == "stop":
                ok, msg = stop_farm(name)
                self._json({"ok": ok, "msg": msg})
            else:
                self._json({"error": "unknown action"}, 404)
            return
        self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    print("dashboard v4: http://0.0.0.0:%d token=%s" % (PORT, TOKEN), flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
