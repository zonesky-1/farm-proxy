#!/usr/bin/env python3
"""webshare_farm.py — farm akun Webshare: signup -> tarik 10 proxy -> stable tier -> ulang.

Setiap akun:
  1. run webshare_auto.py (signup otomatis, exit 0=ok / 3=no-token / 4=reject/429)
  2. archive creds -> ws_creds_archive/<email>.json
  3. GET https://proxy.webshare.io/api/v2/proxy/list/?mode=direct (Token auth, tanpa browser)
  4. append ke ws_farm_proxies.txt + import ke stable tier via webshare_import.py
     (dedup + verify exit-IP -> stable_proxies.txt)
  5. jeda antar akun, repeat

Throttle 429 register: baca "Expected available in N seconds" dari log -> tunggu
(min(N+20, --max-cooldown)) lalu ulang. NO_TOKEN: ulang run (challenge stochastic).

Pakai (dgn venv yg punya webshare_auto deps):
  V=~/.hermes/profiles/syadagentic/farm/venv/bin/python
  $V webshare_farm.py --accounts 3
  $V webshare_farm.py --accounts 1 --max-cooldown 600   # test singkat
"""
import argparse, json, os, re, shutil, subprocess, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
AUTO = os.path.join(HERE, "webshare_auto.py")
IMP = next((p for p in [
    os.path.join(HERE, "webshare_import.py"),
    os.path.expanduser("~/.hermes/profiles/syadagentic/farm/"
                       "proxyfarm/webshare_import.py"),
] if os.path.exists(p)), "")   # fallback: import tool di proxyfarm
LOG = os.path.join(HERE, "ws_run.log")
CREDS = os.path.join(HERE, "ws_creds.json")
ARCH = os.path.join(HERE, "ws_creds_archive")
ALLP = os.path.join(HERE, "ws_farm_proxies.txt")
PY = os.environ.get("VENV_PYTHON", os.path.expanduser("~/.hermes/profiles/syadagentic/farm/venv/bin/python") if os.path.exists(os.path.expanduser("~/.hermes/profiles/syadagentic/farm/venv/bin/python")) else sys.executable)
API = ("https://proxy.webshare.io/api/v2/proxy/list/?mode=direct")
HDRS = {"X-Webshare-User-Agent": "WebshareDashboard/2925706886",
        "Origin": "https://dashboard.webshare.io",
        "Referer": "https://dashboard.webshare.io/"}


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def run_signup(max_cooldown, max_try=3):
    """jalankan webshare_auto.py; return creds dict atau None."""
    for attempt in range(1, max_try + 1):
        log(f"signup attempt {attempt}/{max_try} ...")
        try:
            # stdout di-inherit -> ikut ke log dashboard (live: image rounds, dsb)
            p = subprocess.run([PY, AUTO], cwd=HERE, timeout=900)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = -9
        txt = open(LOG, errors="ignore").read() if os.path.exists(LOG) else ""
        if rc == 0 and os.path.exists(CREDS):
            c = json.load(open(CREDS))
            if c.get("session_token"):
                log("signup OK ->", c.get("email"))
                return c
        m = re.search(r"Expected available in (\d+) seconds", txt)
        if m or rc == 4 and "429" in txt:
            wait = min((int(m.group(1)) if m else 120) + 20, max_cooldown)
            log(f"THROTTLE 429 -> tunggu {wait}s (cap {max_cooldown})")
            time.sleep(wait)
            continue
        log(f"signup gagal rc={rc} -> retry dalam 15s")
        time.sleep(15)
    return None


def fetch_proxies(tok):
    req = urllib.request.Request(API, headers=dict(HDRS, Authorization="Token " + tok))
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    out = []
    for x in data.get("results", []):
        out.append(f'http://{x["username"]}:{x["password"]}@'
                   f'{x["proxy_address"]}:{x["port"]}')
    log(f"proxy list: count={data.get('count')} formatted={len(out)}")
    return out


def archive_creds():
    if not os.path.exists(CREDS):
        return
    os.makedirs(ARCH, exist_ok=True)
    c = json.load(open(CREDS))
    dst = os.path.join(ARCH, (c.get("email") or "unknown").replace("/", "_") + ".json")
    shutil.move(CREDS, dst)
    log("creds archived ->", os.path.basename(dst))


def import_stable(lines):
    if not lines:
        return
    if not IMP:
        log("webshare_import.py ga ketemu -> skip import stable tier")
        return
    tmp = os.path.join(HERE, "_farm_batch.txt")
    open(tmp, "w").write("\n".join(lines) + "\n")
    p = subprocess.run([PY, IMP, tmp], cwd=HERE, timeout=180,
                       capture_output=True, text=True)
    out = (p.stdout or "").strip().splitlines()
    for l in out[-4:]:
        log("import:", l)
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        log("import rc=%d ERR:" % p.returncode, (err[-1] if err else "?")[:160])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", type=int, default=2,
                    help="jumlah akun baru mau dibuat (default 2)")
    ap.add_argument("--max-cooldown", type=int, default=3600,
                    help="cap tunggu throttle 429 dalam detik (default 3600)")
    ap.add_argument("--gap", type=int, default=45,
                    help="jeda antar akun dalam detik (default 45)")
    ap.add_argument("--skip-import", action="store_true",
                    help="jangan sentuh stable tier, cuma kumpulin file")
    a = ap.parse_args()

    if not os.path.exists(AUTO):
        print("webshare_auto.py ga ada di", HERE); return 2

    ok, fail = 0, 0
    for i in range(1, a.accounts + 1):
        log(f"=== akun {i}/{a.accounts} ===")
        creds = run_signup(a.max_cooldown)
        if not creds:
            fail += 1
            log("akun", i, "GAGAL setelah retry")
            continue
        try:
            proxies = fetch_proxies(creds["session_token"])
        except Exception as e:
            log("fetch proxy gagal:", type(e).__name__, str(e)[:120])
            proxies = []
        if proxies:
            with open(ALLP, "a") as f:
                for l in proxies:
                    f.write(l + "\n")
            log(f"+{len(proxies)} proxy -> ws_farm_proxies.txt (total file:",
                str(len(open(ALLP).read().splitlines())) + ")")
            if not a.skip_import:
                import_stable(proxies)
        archive_creds()
        ok += 1
        if i < a.accounts:
            log(f"jeda {a.gap}s sebelum akun berikutnya")
            time.sleep(a.gap)

    log(f"=== SELESAI: {ok} akun sukses, {fail} gagal ===")
    if os.path.exists(ALLP):
        n = len(open(ALLP).read().splitlines())
        log(f"total akumulasi ws_farm_proxies.txt: {n} baris")
    return 0 if ok else 4


if __name__ == "__main__":
    sys.exit(main())
