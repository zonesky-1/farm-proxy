#!/usr/bin/env python3
"""webshare_import.py — import batch proxy Webshare ke stable tier.

CLI (dipanggil webshare_farm.py):
  python3 webshare_import.py <batch_file>   # file: satu proxy per baris, format
                                             # http://user:pass@ip:port

Flow:
  1. baca baris (buang komentar/kosong)
  2. health-check paralel (curl lewat proxy, via api.ipify.org, timeout 8s)
  3. yang LIVE -> dedupe -> append ke proxyfarm/stable_proxies.txt (tier_pool baca ini)
  4. stdout: status (farm log nangkap 4 baris terakhir); rc=0 kalau >=1 live

Rebuilt 2026-10-09 dari kontrak pemakaian (file asli hilang kena prune scratch).
"""
import concurrent.futures as cf
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STABLE = os.path.expanduser(
    "~/.hermes/profiles/syadagentic/farm/proxyfarm/stable_proxies.txt")


def check(px, timeout=8):
    try:
        r = subprocess.run(
            ["curl", "-s", "-m", str(timeout), "-x", px, "https://api.ipify.org"],
            capture_output=True, text=True, timeout=timeout + 3)
        v = (r.stdout or "").strip()
        return bool(v) and v.replace(".", "").isdigit()
    except Exception:
        return False


def main():
    if len(sys.argv) < 2 or not os.path.exists(sys.argv[1]):
        print("usage: webshare_import.py <batch_file>")
        return 2
    lines = []
    for l in open(sys.argv[1]):
        l = l.strip()
        if l and not l.startswith("#") and ("://" in l or ":" in l):
            lines.append(l)
    if not lines:
        print("batch kosong")
        return 1
    print("cek %d proxy..." % len(lines))
    live = []
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(check, px): px for px in lines}
        for f in cf.as_completed(futs):
            if f.result():
                live.append(futs[f])
    print("LIVE %d/%d" % (len(live), len(lines)))
    if not live:
        print("tidak ada yang live -> stable tier tak diubah")
        return 1

    # baca stable lama (dedupe by baris persis)
    old = []
    if os.path.exists(STABLE):
        old = [l.rstrip("\n") for l in open(STABLE)]
    oldset = set(old)
    fresh = [p for p in live if p not in oldset]
    if not fresh:
        print("semua sudah ada di stable tier (total %d)" %
              len([l for l in old if l and not l.startswith("#")]))
        return 0
    # append (pertahankan header komentar di atas)
    os.makedirs(os.path.dirname(STABLE), exist_ok=True)
    with open(STABLE, "a") as f:
        if not old:
            f.write("# stable tier (webshare datacenter) — rebuilt %s\n" %
                    time.strftime("%Y-%m-%d %H:%M"))
        for p in fresh:
            f.write(p + "\n")
    total = len([l for l in open(STABLE)
                 if l.strip() and not l.startswith("#")])
    print("tambah %d baru -> stable tier total %d" % (len(fresh), total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
