#!/usr/bin/env python3
"""lunaproxy_farm.py — massal register akun LunaProxy (luna-proxy.com).

Endpoint (dibuktikan dari bundle homepage):
  POST /api/register {"email","password","telegram","page"}
  -> {ok, email, account}  (0 captcha, session cookie langsung login)

CLI:
  python3 lunaproxy_farm.py --accounts 2 [--backend tml|mailtm]
Output:
  - stdout per akun
  - lunaproxy_creds.jsonl append: {"ts","email","account","password","backend"}
  (password = hasil generate mesin — TIDAK dicetak ke log)
"""
import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = HERE  # captcha-tools (tempmail.py satu direktori)
CREDS = os.path.join(HERE, "lunaproxy_creds.jsonl")
SIG = {"stop": False}


def _sig(*a):
    SIG["stop"] = True


signal.signal(signal.SIGTERM, _sig)
signal.signal(signal.SIGINT, _sig)


def make_inbox(backend, tries=4):
    last = ""
    for t in range(1, tries + 1):
        try:
            r = subprocess.run([sys.executable, os.path.join(TOOLS, "tempmail.py"),
                                "new", backend],
                               capture_output=True, text=True, timeout=60)
            d = json.loads((r.stdout or "{}").strip().splitlines()[-1])
            if d.get("address"):
                return d["address"]
            last = "out=%s err=%s" % (r.stdout[:120], r.stderr[-150:])
        except Exception as e:
            last = str(e)
        print("(inbox try %d gagal: %s)" % (t, last[-160:]), end=" ", flush=True)
        time.sleep(3)
    raise RuntimeError("inbox gagal x%d: %s" % (tries, last[-200:]))


def register(email, password):
    body = json.dumps({"email": email, "password": password,
                       "telegram": "", "page": "/"}).encode()
    req = urllib.request.Request(
        "https://luna-proxy.com/api/register", data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
                 "Origin": "https://luna-proxy.com",
                 "Referer": "https://luna-proxy.com/"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read().decode())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", type=int, default=1)
    ap.add_argument("--backend", default="tml", choices=["tml", "mailtm"])
    a = ap.parse_args()
    print("=== lunaproxy farm: %d akun (inbox %s) ===" % (a.accounts, a.backend),
          flush=True)
    ok = 0
    for i in range(1, a.accounts + 1):
        if SIG["stop"]:
            print("stop di minta")
            break
        print("[akun %d/%d] minta inbox ..." % (i, a.accounts), end=" ",
              flush=True)
        try:
            email = make_inbox(a.backend)
            pw = "Lp!F" + secrets.token_hex(8) + "9x"
            print(email, end=" -> ", flush=True)
            d = register(email, pw)
            acct = d.get("account")
            if not (d.get("ok") and acct):
                print("GAGAL (%s)" % json.dumps(d)[:140])
                continue
            rec = {"ts": time.time(), "email": email, "account": acct,
                   "password": pw, "backend": a.backend}
            with open(CREDS, "a") as f:
                f.write(json.dumps(rec) + "\n")
            ok += 1
            print("OK (%s)" % acct)
        except urllib.error.HTTPError as e:
            print("HTTP %s %s" % (e.code, e.read().decode()[:120]))
        except Exception as e:
            print("ERR %s" % e)
        if i < a.accounts:
            time.sleep(2)
    print("=== SELESAI: %d akun sukses, %d gagal ===" % (ok, a.accounts - ok),
          flush=True)
    if os.path.exists(CREDS):
        print("total akumulasi %s: %d baris" % (
            CREDS, sum(1 for _ in open(CREDS))), flush=True)


if __name__ == "__main__":
    main()
