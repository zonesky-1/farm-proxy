#!/usr/bin/env python3
"""ProxyScrape signup v2 (pola Webshare full chain):
inbox -> mint turnstile -> register -> baca kode verifikasi -> verify -> create API key -> claim trial.
Password/kode TIDAK dicetak. Hasil ke stdout + ps_creds_v2.json."""
import subprocess, secrets, json, time, os, sys, re

BASE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.environ.get("TOOLS_DIR", BASE if os.path.exists(os.path.join(BASE, "tempmail.py")) else os.path.expanduser("~/.hermes/profiles/syadagentic/farm/captcha-tools"))
VENV = os.environ.get("VENV_PYTHON", os.path.expanduser("~/.hermes/profiles/syadagentic/farm/venv/bin/python") if os.path.exists(os.path.expanduser("~/.hermes/profiles/syadagentic/farm/venv/bin/python")) else sys.executable)
RESI = os.environ.get("RESI_FILE", os.path.join(BASE, "residential_proxies.txt") if os.path.exists(os.path.join(BASE, "residential_proxies.txt")) else os.path.expanduser("~/.hermes/profiles/syadagentic/farm/proxyfarm/residential_proxies.txt"))
URL = "https://dashboard.proxyscrape.com/v2/sign-up"
SITEKEY = "0x4AAAAAAAFWUVCKyusT9T8r"
API = "https://dashboard.proxyscrape.com/v2"
TOKEN_FILE = "/tmp/turnstile_token.txt"

sys.path.insert(0, TOOLS)
from tempmail import TempBox  # noqa: E402


def proxies():
    out = []
    if not os.path.exists(RESI):
        return out
    for l in open(RESI):
        l = l.strip()
        if l and not l.startswith("#") and l.startswith(("socks5://", "http://")):
            out.append(l)
    return out


def alive(px):
    try:
        r = subprocess.run(["curl", "-s", "-m", "7", "-x", px, "https://api.ipify.org"],
                           capture_output=True, text=True, timeout=10)
        v = r.stdout.strip()
        return bool(v) and v.replace(".", "").isdigit()
    except Exception:
        return False


def mint(px):
    if os.path.exists(TOKEN_FILE):
        os.unlink(TOKEN_FILE)
    env = dict(os.environ, TURNSTILE_PROXY=px)
    try:
        r = subprocess.run([VENV, os.path.join(BASE, "solve_turnstile.py"), URL, SITEKEY],
                           capture_output=True, text=True, timeout=150, cwd=BASE, env=env)
        ok = "RESULT=PASS" in (r.stdout + r.stderr)
        tok = open(TOKEN_FILE).read().strip() if os.path.exists(TOKEN_FILE) else ""
        return ok and bool(tok)
    except Exception:
        return False


def api(method, path, tok, payload=None, form=False, timeout=25):
    cmd = ["curl", "-s", "-m", str(timeout), "-X", method, API + path,
           "-H", "Authorization: Bearer " + tok,
           "-H", "Origin: https://dashboard.proxyscrape.com",
           "-H", "Referer: https://dashboard.proxyscrape.com"]
    if form:
        for k, v in (payload or {}).items():
            cmd += ["-F", f"{k}={v}"]
    elif payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
    return r.stdout.strip()


def main():
    # 1) inbox (rotasi otomatis jika rate limit 429)
    b = None
    pref = os.environ.get("PS_BACKEND", "mailtm")
    backends = [pref] + [x for x in ("mailtm", "tml") if x != pref]
    for backend in backends:
        try:
            b = TempBox(backend)
            email = b.address
            break
        except Exception as e:
            print("[inbox:%s] gagal: %s" % (backend, e), flush=True)
            time.sleep(1)
    if not b:
        print("RESULT=FAIL inbox: semua backend rate limit/mati", flush=True)
        return
    print("[inbox:%s]" % b.b, email, flush=True)

    # 2) mint via proxy residential
    cands = proxies()
    secrets.SystemRandom().shuffle(cands)
    tok_reg = None
    for px in cands[:20]:
        if not alive(px):
            continue
        print("[mint] via", px, flush=True)
        if mint(px):
            tok_reg = open(TOKEN_FILE).read().strip()
            print("[mint] PASS", flush=True)
            break
        print("[mint] gagal, ganti proxy", flush=True)
    if not tok_reg:
        print("RESULT=FAIL (mint habis)", flush=True)
        return

    # 3) register
    pw = "Ps!" + secrets.token_urlsafe(14)
    resp = api("POST", "/v4/account/auth/register", "", {"email": email, "password": pw,
                                                          "cf_turnstile_token": tok_reg}, form=True)
    try:
        reg = json.loads(resp)
    except Exception:
        reg = {}
    access = reg.get("access_token")
    if not access:
        print("RESULT=FAIL register:", resp[:300], flush=True)
        return
    print("[register] OK -> id", reg.get("userData", {}).get("id"), flush=True)
    # pastikan kode terkirim (register mungkin ga auto-send utk domain tertentu)
    rs = api("POST", "/v4/account/reset-verification-code", access, {})
    print("[resend]", rs[:120], flush=True)

    # 4) tunggu kode verifikasi di inbox
    code = None
    try:
        code = b.wait_otp(seconds=240)
    except Exception as e:
        print("[otp] wait error:", e, flush=True)
    if not code:
        print("RESULT=FAIL (kode verifikasi ga masuk)", flush=True)
        json.dump({"email": email, "password": pw, "access_token": access},
                  open(os.path.join(BASE, "ps_creds_v2.json"), "w"), indent=1)
        return
    code = re.sub(r"\D", "", str(code))[:8] or str(code)
    print("[otp] diterima (len=%d)" % len(code), flush=True)

    # 5) verify email
    v = api("POST", "/v4/account/verify-email", access, {"verificationCode": code}, form=True)
    print("[verify]", v[:220], flush=True)

    # 6) summary + api-keys + trial
    summ = api("GET", "/v4/account/accounts-summary", access)
    print("[summary]", summ[:280], flush=True)
    keys = api("GET", "/v4/account/api-keys?page=1&limit=10", access)
    print("[keys-list]", keys[:300], flush=True)
    created = None
    if '"total"' in keys and '"total":0' in keys.replace(" ", ""):
        for payload in [{"name": "farm1"}, {"name": "farm1", "permissions": ["read"]}]:
            created = api("POST", "/v2/v4/account/api-keys" if False else "/v4/account/api-keys",
                          access, payload)
            print("[keys-create]", created[:300], flush=True)
            if created and "error" not in created[:60].lower():
                break
    trial = api("POST", "/v4/account/premium/claim-trial", access, {})
    print("[trial]", trial[:240], flush=True)

    json.dump({"email": email, "password": pw, "access_token": access,
               "verify": v[:400], "summary": summ[:600], "keys": keys[:600],
               "created": (created or "")[:600], "trial": trial[:400]},
              open(os.path.join(BASE, "ps_creds_v2.json"), "w"), indent=1)
    print("RESULT=OK creds -> ps_creds_v2.json", flush=True)


if __name__ == "__main__":
    main()
