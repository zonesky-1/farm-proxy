#!/usr/bin/env python3
"""
PROXY BEAST v2.0 — Enterprise-Grade High-Concurrency HTTPS Proxy Farm & Validator
Engine: asyncio + aiohttp (1,000 workers) + local MaxMind GeoIP + Multi-feed Ranking
Author: SYADAGENTIC Farm Stack
"""

import os
import sys
import re
import time
import json
import argparse
import asyncio
import aiohttp
import resource
import urllib.request
from collections import Counter
import maxminddb

# Raise file descriptor limit
try:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target_limit = min(65535, hard if hard > 0 else 65535)
    resource.setrlimit(resource.RLIMIT_NOFILE, (target_limit, hard))
except Exception as e:
    pass

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORTS_DIR = os.path.join(BASE_DIR, "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)

OUT_ALL_TXT = os.path.join(EXPORTS_DIR, "https_verified_alive.txt")
OUT_JSON = os.path.join(EXPORTS_DIR, "https_alive_detailed.json")
OUT_US_TXT = os.path.join(EXPORTS_DIR, "https_verified_us.txt")
OUT_SG_TXT = os.path.join(EXPORTS_DIR, "https_verified_sg.txt")
OUT_ID_TXT = os.path.join(EXPORTS_DIR, "https_verified_id.txt")

_def_mmdb = "/home/ubuntu/.cache/camoufox/geoip/mmdb/geoip aio by daijro-combined.mmdb"
_alt_mmdb = os.path.expanduser("~/.cache/camoufox/geoip/mmdb/geoip aio by daijro-combined.mmdb")
MMDB_PATH = os.environ.get("MMDB_PATH", _alt_mmdb if os.path.exists(_alt_mmdb) else _def_mmdb)
TARGET_URL = "https://api.ipify.org?format=json"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
IPPORT_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}:\d{2,5}\b')

# 35+ Aggregated Public Feeds
SOURCES = [
    # Proxifly (hourly fresh)
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/http/data.txt",
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/protocols/https/data.txt",
    "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.txt",
    # ProxyScrape (API v4, API v2, GH mirror)
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text",
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http",
    "https://cdn.jsdelivr.net/gh/proxyscrape/free-proxy-list@main/proxies/all/data.txt",
    # Monosans
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/all.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
    # TheSpeedX
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/https.txt",
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt",
    # Roosterkid
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS.txt",
    # Zevtyardt Mega List (100k+)
    "https://raw.githubusercontent.com/zevtyardt/proxy-list/main/http.txt",
    "https://raw.githubusercontent.com/zevtyardt/proxy-list/main/all.txt",
    # ErcinDG
    "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/http.txt",
    "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/https.txt",
    "https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/socks5.txt",
    # Sunny9577
    "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt",
    # Anonym0usWork1221
    "https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/main/proxy_files/http_proxies.txt",
    "https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/main/proxy_files/https_proxies.txt",
    # ShiftyTR
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt",
    "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
    # Zloi-user
    "https://raw.githubusercontent.com/zloi-user/hideip.me/master/https.txt",
    "https://raw.githubusercontent.com/zloi-user/hideip.me/master/http.txt",
    # MuRongPIG
    "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt",
    # OfficialPutuid
    "https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/http/http.txt",
    "https://raw.githubusercontent.com/officialputuid/KangProxy/KangProxy/https/https.txt",
    # HookzAG
    "https://raw.githubusercontent.com/HookzAG/Proxy-List/master/http.txt",
    # Clarketm
    "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
    # Jetkai Master Archive (48k+)
    "https://raw.githubusercontent.com/jetkai/proxy-list/master/archive/txt/proxies.txt",
    # Vakhov Fresh
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/https.txt",
    "https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/http.txt",
]

def get_direct_ip():
    """Fetch direct VPS IP to detect proxy leaks"""
    try:
        req = urllib.request.Request("https://api.ipify.org?format=json", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8")).get("ip")
    except Exception as e:
        print(f"[!] Warning: Gagal get direct IP ({e}), fallback ke default")
        return "69.79.101.97"

def harvest_and_rank():
    """Scrape, dedup, and rank candidates by frequency across feeds"""
    t0 = time.time()
    counter = Counter()
    print(f"[*] [HARVEST] Mengunduh proxy list dari {len(SOURCES)} sumber publik...", flush=True)

    def fetch_url(url):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=12) as r:
                content = r.read().decode("utf-8", errors="ignore")
                matches = set(IPPORT_RE.findall(content))
                return url, matches
        except Exception:
            return url, set()

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = [executor.submit(fetch_url, u) for u in SOURCES]
        for f in concurrent.futures.as_completed(futures):
            url, found = f.result()
            counter.update(found)
            tag = url.split("/")[-1] or url.split("/")[-2]
            print(f"  [+] {tag[:28]:<28} : {len(found):>6} candidates (Total unik: {len(counter):,})", flush=True)

    elapsed = time.time() - t0
    # Ranked by occurrence count descending (freshest / most confirmed first)
    ranked = [ip for ip, _ in counter.most_common()]
    print(f"\n[✓] [HARVEST SELESAI] Total: {len(ranked):,} IP:PORT unik dalam {elapsed:.2f} detik.")
    multi_cnt = sum(1 for _, c in counter.items() if c >= 2)
    print(f"[*] Kandidat multi-feed (skor freshness tinggi): {multi_cnt:,}\n", flush=True)
    return ranked

class ProxyValidator:
    def __init__(self, direct_ip, concurrency=1000, timeout=3.0):
        self.direct_ip = direct_ip
        self.concurrency = concurrency
        self.timeout = timeout
        self.geo_db = None
        if os.path.exists(MMDB_PATH):
            try:
                self.geo_db = maxminddb.open_database(MMDB_PATH)
            except Exception as e:
                print(f"[!] Warning: MMDB load fail: {e}")

        self.alive_results = []
        self.checked_count = 0
        self.total_targets = 0
        self.t0 = 0

    def get_country(self, ip):
        if not self.geo_db:
            return "UNKNOWN"
        try:
            res = self.geo_db.get(ip)
            if res and "country" in res:
                return res["country"].get("iso_code", "UNKNOWN")
        except Exception:
            pass
        return "UNKNOWN"

    def _append_live_file(self, res):
        with open(OUT_ALL_TXT, "a") as f:
            f.write(f"{res['proxy']}\n")

        cc = res["country"]
        if cc == "US":
            with open(OUT_US_TXT, "a") as f:
                f.write(f"{res['proxy']}\n")
        elif cc == "SG":
            with open(OUT_SG_TXT, "a") as f:
                f.write(f"{res['proxy']}\n")
        elif cc == "ID":
            with open(OUT_ID_TXT, "a") as f:
                f.write(f"{res['proxy']}\n")

    async def worker(self, queue, session):
        client_timeout = aiohttp.ClientTimeout(total=self.timeout, connect=self.timeout - 1.0)
        while True:
            try:
                proxy = await queue.get()
            except asyncio.CancelledError:
                break

            p_url = f"http://{proxy}"
            t_start = time.time()
            try:
                async with session.get(TARGET_URL, proxy=p_url, timeout=client_timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        exit_ip = data.get("ip")
                        if exit_ip and exit_ip != self.direct_ip:
                            latency_ms = round((time.time() - t_start) * 1000)
                            country = self.get_country(exit_ip)
                            result = {
                                "proxy": p_url,
                                "hostport": proxy,
                                "egress_ip": exit_ip,
                                "country": country,
                                "latency_ms": latency_ms,
                                "verified_at": int(time.time())
                            }
                            self.alive_results.append(result)
                            self._append_live_file(result)
            except Exception:
                pass
            finally:
                self.checked_count += 1
                if self.checked_count % 5000 == 0 or self.checked_count == self.total_targets:
                    rate = self.checked_count / max(0.1, time.time() - self.t0)
                    alive_pct = (len(self.alive_results) / self.checked_count) * 100
                    print(
                        f"  [Progress] {self.checked_count:,}/{self.total_targets:,} "
                        f"({rate:.0f} req/s) | LIVE: {len(self.alive_results):,} ({alive_pct:.2f}%)",
                        flush=True
                    )
                queue.task_done()

    async def run(self, candidate_list):
        self.total_targets = len(candidate_list)
        self.checked_count = 0
        self.alive_results = []
        self.t0 = time.time()

        # Clear old output files
        for p in [OUT_ALL_TXT, OUT_US_TXT, OUT_SG_TXT, OUT_ID_TXT]:
            if os.path.exists(p):
                os.remove(p)

        print(f"[*] [VALIDASI] Target pool: {self.total_targets:,} candidates")
        print(f"[*] Engine: {self.concurrency} async workers | Timeout: {self.timeout}s | Anti-Leak VPS IP: {self.direct_ip}")
        print(f"[*] Target Endpoint: {TARGET_URL} (TLS Handshake Port 443)\n", flush=True)

        queue = asyncio.Queue(maxsize=10000)
        connector = aiohttp.TCPConnector(
            limit=self.concurrency + 200,
            ssl=False,
            ttl_dns_cache=300,
            use_dns_cache=True,
            force_close=True
        )

        async with aiohttp.ClientSession(connector=connector) as session:
            # Spawn worker pool
            workers = [asyncio.create_task(self.worker(queue, session)) for _ in range(self.concurrency)]

            # Feed queue
            for p in candidate_list:
                await queue.put(p)

            # Wait for all items to complete
            await queue.join()

            # Cancel workers
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        elapsed = time.time() - self.t0
        avg_rate = self.total_targets / max(0.1, elapsed)
        print(f"\n[✓] [VALIDASI SELESAI]")
        print(f"  • Waktu Tempuh : {elapsed:.1f} detik ({avg_rate:.0f} req/detik)")
        print(f"  • Total LIVE   : {len(self.alive_results):,} dari {self.total_targets:,} proxy teruji.")

        # Summary
        countries = Counter(x["country"] for x in self.alive_results)
        print("  • Distribusi Top Negara:", dict(countries.most_common(10)))

        # Output JSON
        with open(OUT_JSON, "w") as f:
            json.dump({
                "timestamp": int(time.time()),
                "total_checked": self.total_targets,
                "total_alive": len(self.alive_results),
                "elapsed_seconds": round(elapsed, 2),
                "countries": dict(countries),
                "proxies": sorted(self.alive_results, key=lambda x: x["latency_ms"])
            }, f, indent=2)

        return self.alive_results

def main():
    parser = argparse.ArgumentParser(description="Proxy Beast v2.0 Enterprise Scanner")
    parser.add_argument("--limit", type=int, default=0, help="Jumlah candidate yang dicek (0 = SEMUA / FULL SCAN)")
    parser.add_argument("--concurrency", type=int, default=1000, help="Jumlah worker paralel (default: 1000)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Timeout connect per proxy (default: 3.0s)")
    args = parser.parse_args()

    direct_ip = get_direct_ip()
    candidates = harvest_and_rank()

    if args.limit > 0 and args.limit < len(candidates):
        print(f"[*] Membatasi ke TOP {args.limit:,} kandidat...")
        candidates = candidates[:args.limit]

    validator = ProxyValidator(direct_ip, concurrency=args.concurrency, timeout=args.timeout)
    asyncio.run(validator.run(candidates))

if __name__ == "__main__":
    main()
