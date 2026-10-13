#!/usr/bin/env python3
"""
TOOL 2: Solve Turnstile pakai sitekey (via Camoufox native render).
Usage:  python3 solve_turnstile.py <url> [sitekey]

Cara kerja (skill camoufox-turnstile-automation):
  - Camoufox render halaman, turnstile auto-solve kalau IP punya baseline trust.
  - Submit button jadi enabled setelah token populate (~645 chars) di
    input[name=cf-turnstile-response].
  - Tool ini menunggu token, lalu:
      a) PRINT token mentah ke stdout (buat dipakai program lain), DAN
      b) simpan ke /tmp/turnstile_token.txt, DAN
      c) kalau ada tombol, klik submit biar flow lanjut.
  - Opsional env TURNSTILE_OUT=<path> utk nimpuk lokasi output.

Kalau sitekey di-skip, tool ini auto-scan dari DOM/data-sitekey.
Exit code 0 = dapat token; 1 = gagal/timeout.

PENTING (dari skill):
  - IP datacenter bisa gak dapet trust -> token tetep kosong. Butuh proxy/rotasi.
  - Proxy: env TURNSTILE_PROXY="socks5://user:pass@host:port"
"""
import os, sys, time, json

def load_sitekey_from_page(page):
    res = page.evaluate("""() => {
        const el = document.querySelector('[data-sitekey]');
        return el ? el.getAttribute('data-sitekey') : null;
    }""")
    return res

def solve(page, sitekey):
    # situs lazy-render widget (ga ada di DOM pas load) -> paksa: load api.js
    # + render explicit. Callback simpan token ke field DAN window.__ts_tok
    # (bug lama: render doang tanpa load script & token cuma console.log ->
    #  polling field selalu kosong walau solve sukses = 0 trusted)
    if sitekey:
        try:
            page.evaluate("""async (sk) => {
                if (!window.turnstile) {
                    await new Promise((res, rej) => {
                        const s = document.createElement('script');
                        s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
                        const t = setTimeout(() => rej(new Error('api.js timeout')), 8000);
                        s.onload = () => { clearTimeout(t); res(); };
                        s.onerror = () => { clearTimeout(t); rej(new Error('api.js onerror')); };
                        document.head.appendChild(s);
                    });
                }
                if (!document.getElementById('__ts')) {
                    const d = document.createElement('div');
                    d.id = '__ts';
                    d.style.cssText = 'position:fixed;left:8px;bottom:8px;z-index:99999;';
                    document.body.appendChild(d);
                }
                try { window.turnstile.render('#__ts', {sitekey: sk, callback: function(t){
                    window.__ts_tok = t;
                    let f = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
                    if (!f) { f = document.createElement('input'); f.type='hidden'; f.name='cf-turnstile-response';
                              document.body.appendChild(f); }
                    f.value = t;
                }}); } catch(e) { window.__ts_err = String(e); }
            }""", sitekey)
        except Exception as e:
            print(f"[solve] evaluate err: {str(e)[:150]}")
        dbg = page.evaluate("""() => ({
            ts: typeof window.turnstile,
            div: !!document.getElementById('__ts'),
            ifr: document.querySelectorAll('iframe').length,
            err: window.__ts_err || null
        })""")
        print(f"[solve] dbg {dbg}")

    # poll: field ASLI situs ATAU token hasil render manual kita
    deadline = time.time() + 110
    t0 = time.time()
    clicked = False
    submitted = False
    token = ""
    while time.time() < deadline:
        token = page.evaluate("""() => {
            const f = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
            return (f && f.value && f.value.length > 50) ? f.value : (window.__ts_tok || '');
        }""")
        if token and len(token) > 50:
            break
        el = time.time() - t0
        if not submitted and el > 10:
            ifr = page.evaluate("() => document.querySelectorAll(\"iframe[src*='challenges.cloudflare.com']\").length")
            if not ifr:
                try:
                    import random as _r
                    page.fill("input[type=email], input[name=email]",
                              "th%d@maxxspace.com" % _r.randint(100000, 999999))
                    page.fill("input[type=password], input[name=password]",
                              "Aa1!" + _r.choice(["qz7xk2m9", "hj4vn8p3", "wq9be5t1"]))
                    page.click("button[type=submit], button:has-text('Create account')")
                    submitted = True
                    print("SUBMIT utk trigger lazy widget")
                except Exception as e:
                    print("submit err:", str(e)[:80])
                    submitted = True
        if not clicked and el > 25:
            try:
                box = page.locator("iframe[src*='challenges.cloudflare.com']").first.bounding_box()
                if box and box.get("width"):
                    page.mouse.click(box["x"] + 28, box["y"] + box["height"] / 2)
                    clicked = True
                    print("CLICKED checkbox")
            except Exception:
                pass
        if int(el) % 15 < 1 and int(el) > 0:
            n_ifr = page.evaluate("() => document.querySelectorAll(\"iframe\").length")
            print(f"[solve] t={int(el)}s iframe={n_ifr} tok={len(token or '')}")
        time.sleep(1)

    if token:
        print(f"TOKEN={token[:80]}... (len={len(token)})")
        out = os.environ.get("TURNSTILE_OUT", "/tmp/turnstile_token.txt")
        with open(out, "w") as f:
            f.write(token)
        print(f"SAVED={out}")
        return token
    return ""

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    url = sys.argv[1]
    sitekey = sys.argv[2] if len(sys.argv) > 2 else None

    from camoufox.sync_api import Camoufox
    os.environ["DISPLAY"] = ":99"

    proxy = os.environ.get("TURNSTILE_PROXY")
    kw = {"headless": False, "humanize": True, "geoip": True if not proxy else False}
    if proxy:
        import urllib.parse as up
        pu = up.urlparse(proxy)
        if pu.username:
            # playwright nolak user:pass di server -> pisah field
            kw["proxy"] = {"server": "%s://%s:%s" % (pu.scheme, pu.hostname or "", pu.port or ""),
                           "username": pu.username, "password": pu.password or ""}
        else:
            kw["proxy"] = {"server": proxy}

    with Camoufox(**kw) as b:
        page = b.new_context(viewport={"width": 1280, "height": 900}).new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=25000)
        time.sleep(3)

        if not sitekey:
            sitekey = load_sitekey_from_page(page)
            print(f"Auto sitekey = {sitekey}")

        token = solve(page, sitekey)
        # screenshot biar bisa diinspeksi
        page.screenshot(path="/tmp/turnstile_last.png", full_page=False)

        if token:
            print("RESULT=PASS")
            sys.exit(0)
        else:
            print("RESULT=FAIL (IP gak dapet trust / proxy needed)")
            sys.exit(1)

if __name__ == "__main__":
    main()
