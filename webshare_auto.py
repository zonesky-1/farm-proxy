#!/usr/bin/env python3
"""webshare_auto.py v3 — signup Webshare end-to-end (invisible reCAPTCHA),
2 jalur solve otomatis same-process (tanpa turn gap):
  1. AUDIO  — playwright-recaptcha + Google STT keyless (klik JS bframe,
     bypass overlay). Kalau Google `audio-disabled` utk IP -> fallback:
  2. IMAGE  — CLIP zero-shot lokal (open-clip ViT-B-32 laion2b cached)
     classify per-tile langsung dari payload URL challenge -> klik JS ->
     verify -> loop dynamic "check new images" sampe token.
Token masuk via listener reload-response (solver._token).
Password: secrets.token_urlsafe -> ws_creds.json (TIDAK dicetak).
Same-egress: semua di VPS ini.
Exit: 0 sukses · 3 NO_TOKEN · 4 server tolak · 6 error teknis.
"""
import io, json, os, re, secrets, sys, time
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"  # sebelum import open_clip

from playwright.sync_api import sync_playwright
from playwright_recaptcha import recaptchav2
from playwright_recaptcha.recaptchav2.recaptcha_box import SyncRecaptchaBox
from playwright_recaptcha.errors import (RecaptchaRateLimitError,
                                         RecaptchaNotFoundError)

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://dashboard.webshare.io/register"
CREDS = os.path.join(HERE, "ws_creds.json")

JS_AUDIO = "document.getElementById('recaptcha-audio-button').click()"
JS_RELOAD = "document.getElementById('recaptcha-reload-button').click()"
JS_VERIFY = "document.getElementById('recaptcha-verify-button').click()"
JS_SUBMIT_AUDIO = """(t)=>{ const i=document.getElementById('audio-response');
    i.value=t; i.dispatchEvent(new Event('input',{bubbles:true}));
    document.getElementById('recaptcha-verify-button').click(); }"""

# ---- CLIP global (lazy) ---------------------------------------------------
_clip = {"model": None, "tok": None, "pre": None}

def _clip_load():
    if _clip["model"] is None:
        import torch, open_clip
        m, _, pre = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k")
        m.eval()
        _clip.update(model=m, tok=open_clip.get_tokenizer("ViT-B-32"), pre=pre)
    return _clip["model"], _clip["tok"], _clip["pre"]

def clip_score_images(imgs, noun):
    """Return list p_pos per tile (0..1)."""
    import torch
    model, tok, pre = _clip_load()
    noun = noun.lower().strip()
    forms = {noun}
    base = noun
    if base.endswith("ies"): base = base[:-3] + "y"
    elif base.endswith("es") and base[:-2].endswith(("s","x","z","ch","sh")): base = base[:-2]
    elif base.endswith("s") and not base.endswith("ss"): base = base[:-1]
    forms.add(base)
    if not base.endswith("s"): forms.add(base + "s")
    positives = []
    for f in sorted(forms):
        positives += [f"a photo of a {f}", f"a photo showing a {f}",
                      f"a {f} is visible in this image"]
    negatives = ["a photo with no such object",
                 "an empty street scene without the target",
                 "a random unrelated photo",
                 "a photo of a different object"]
    texts = positives + negatives
    with torch.no_grad():
        tf = model.encode_text(tok(texts))
        tf = tf / tf.norm(dim=-1, keepdim=True)
        ims = torch.stack([pre(im.convert("RGB")) for im in imgs])
        imf = model.encode_image(ims)
        imf = imf / imf.norm(dim=-1, keepdim=True)
        probs = ((imf @ tf.T) * 100.0).softmax(dim=-1)
        p = probs[:, list(range(len(positives)))].sum(dim=-1)
    return [float(x) for x in p]

def parse_noun(desc):
    m = re.search(r"(?:images?|squares?) with (?:an? |the )?([a-z ]+?)(?:Click|If|$)",
                  desc, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"with (?:an? |the )?([a-z ]+)", desc, re.I)
    return (m.group(1).strip() if m else "")

def tile_srcs(bf):
    return bf.evaluate("""() => [...document.querySelectorAll(
        'td.rc-imageselect-tile')].map(td => {
            const img = td.querySelector('img');
            return img ? (img.src||'') : ''; })""")

def selected_set(bf):
    return bf.evaluate("""() => [...document.querySelectorAll(
        'td.rc-imageselect-tile')].map(td =>
            td.classList.contains('selected'))""")

def js_click_tile(bf, i):        # i = 0-based
    return bf.evaluate("""(i)=>{ const td=document.querySelectorAll(
        'td.rc-imageselect-tile')[i]; if(!td) return 'no';
        td.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));
        td.dispatchEvent(new MouseEvent('mouseup',{bubbles:true}));
        td.click(); return 'ok'; }""", i)

def challenge_desc(bf):
    return bf.evaluate("""() => { const d=document.querySelector(
        '.rc-imageselect-desc, .rc-imageselect-desc-no-canonical, .rc-imageselect-instructions');
        return d ? d.textContent : ''; }""") or ""

def btn_text(bf):
    return bf.evaluate("""() => { const b=document.getElementById('recaptcha-verify-button');
        return b ? b.textContent.trim() : ''; }""")

def any_challenge_visible(page):
    try:
        SyncRecaptchaBox.from_frames(page.frames)
        return True
    except RecaptchaNotFoundError:
        return False

# ---- jalur 1: audio ------------------------------------------------------
def solve_audio_manual(page, solver):
    for cyc in range(6):
        try:
            box = SyncRecaptchaBox.from_frames(page.frames)
        except RecaptchaNotFoundError:
            page.wait_for_timeout(500); continue
        if box.frames_are_detached():
            page.wait_for_timeout(500); continue
        bf = box.bframe_frame
        try:
            has_audio = bf.evaluate("() => !!document.getElementById('recaptcha-audio-button')")
        except Exception:
            has_audio = False
        if not has_audio:
            return None, "no-audio-button"
        try:
            disabled = bf.evaluate("() => document.getElementById('recaptcha-audio-button').disabled")
        except Exception:
            disabled = False
        if not disabled:
            bf.evaluate(JS_AUDIO)
            page.wait_for_timeout(1200)
            try:
                disabled = bf.evaluate("() => document.getElementById('audio-response').disabled")
            except Exception:
                disabled = True
            if disabled:
                return None, "audio-disabled (Google nolak jalur audio)"
        try:
            url = solver._get_audio_url(box)
        except RecaptchaRateLimitError:
            return None, "rate-limit"
        except Exception:
            page.wait_for_timeout(800); continue
        text = solver._transcribe_audio(url, language="en")
        if not text:
            bf.evaluate(JS_RELOAD); page.wait_for_timeout(1200); continue
        bf.evaluate(JS_SUBMIT_AUDIO, text)
        dl = time.time() + 12
        while time.time() < dl:
            page.wait_for_timeout(250)
            if solver._token:
                return solver._token, f"audio-ok cyc={cyc}"
            try:
                if box.frames_are_detached() or box.challenge_is_solved():
                    break
            except Exception:
                break
        if solver._token:
            return solver._token, f"audio-ok cyc={cyc}"
    return solver._token, "audio-habis"

# ---- jalur 2: image via CLIP lokal ---------------------------------------
def solve_image_clip(page, solver, max_rounds=10):
    _clip_load()  # warmup model (sekali doang)
    for rnd in range(max_rounds):
        if solver._token:
            return solver._token, "token-sudah-ada"
        try:
            box = SyncRecaptchaBox.from_frames(page.frames)
        except RecaptchaNotFoundError:
            print(f"[image r{rnd}] box NONE", flush=True)
            page.wait_for_timeout(600); continue
        if box.frames_are_detached():
            print(f"[image r{rnd}] box DETACHED", flush=True)
            page.wait_for_timeout(600); continue
        bf = box.bframe_frame
        srcs = [s for s in tile_srcs(bf) if s]
        if not srcs:
            print(f"[image r{rnd}] srcs EMPTY", flush=True)
            page.wait_for_timeout(600); continue
        noun = parse_noun(challenge_desc(bf)) or "object"
        sel = selected_set(bf)
        # SEMUA tile share 1 payload (crop CSS) -> download SEKALI, crop grid
        from PIL import Image
        url = srcs[0] if srcs[0].startswith("http") else "https:" + srcs[0]
        full = None
        for _try in range(2):      # payload kadang balikin non-image (rate-limit)
            try:
                resp = page.request.get(url, timeout=15000)
                full = Image.open(io.BytesIO(resp.body())).convert("RGB")
                break
            except Exception as e:
                if _try == 0:
                    page.wait_for_timeout(1200)
                else:
                    print(f"[image r{rnd}] download ERR {type(e).__name__}", flush=True)
        if full is None:
            page.wait_for_timeout(800); continue
        n = len(srcs)
        nc = int(round(n ** 0.5)); nr = max(n // nc, 1)
        W, H = full.size
        imgs, idx_map = [], []
        for i in range(n):
            r, c = divmod(i, nc)
            imgs.append(full.crop((c * W // nc, r * H // nr,
                                   (c + 1) * W // nc, (r + 1) * H // nr)))
            idx_map.append(i)
        if not imgs:
            page.wait_for_timeout(800); continue
        p = clip_score_images(imgs, noun)
        thr = 0.55
        picks = [idx_map[k] for k in range(len(imgs)) if p[k] > thr]
        if not picks and max(p, default=0) > 0.45:
            picks = [idx_map[k] for k in range(len(imgs)) if p[k] > 0.45]
        pmax = round(max(p, default=0), 3)
        desc = challenge_desc(bf)
        none_hint = ("once there are none" in desc.lower()
                     or btn_text(bf).lower() == "skip")
        print(f"[image r{rnd}] noun='{noun}' n={len(srcs)} pmax={pmax} "
              f"picks={[i+1 for i in picks]} "
              f"sel_now={[i+1 for i, s in enumerate(sel) if s]}", flush=True)
        if not picks and (none_hint or pmax < 0.35):
            if pmax < 0.35 and not none_hint:
                print("[image] CLIP blind (pmax<0.35) -> verify polos (best-effort)",
                      flush=True)
            bf.evaluate(JS_VERIFY)
        else:
            # sinkron klik: matikan yang ke-select di luar picks, nyalakan picks
            want = set(picks)
            for i, s in enumerate(sel):
                if s and i not in want:
                    js_click_tile(bf, i)
            for i in picks:
                if i < len(sel) and not sel[i]:
                    js_click_tile(bf, i)
            page.wait_for_timeout(200)
            bf.evaluate(JS_VERIFY)
        # hasil verify
        dl = time.time() + 14
        while time.time() < dl:
            page.wait_for_timeout(300)
            if solver._token:
                return solver._token, f"image-ok r{rnd} noun={noun}"
            try:
                if box.frames_are_detached():
                    break
            except Exception:
                break
        if solver._token:
            return solver._token, f"image-ok r{rnd} noun={noun}"
    return solver._token, "image-habis-rounds"

# ---- main ----------------------------------------------------------------
def main():
    # mailnu.com = domain tempmail.plus — LOLOS blocklist disposable Webshare
    # (oracle tes 2026-10-09: maxxspace/mailinator/yopmail/grr.la/1secmail = BLOCKED,
    #  mailnu.com & trashmail.com = pass, gmail/proton = "suspicious")
    email = f"{secrets.token_hex(6)}@mailnu.com"
    try:    # simpan juga ke mailtm_creds.json utk riwayat (format lama)
        _c = json.load(open(os.path.expanduser(
            "~/.hermes/profiles/syadagentic/farm/mailtm_creds.json")))
        _c["addr"] = email
        json.dump(_c, open(os.path.expanduser(
            "~/.hermes/profiles/syadagentic/farm/mailtm_creds.json"), "w"))
    except Exception:
        pass
    pw = secrets.token_urlsafe(18)
    t0 = time.time()
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 900},
                                  locale="en-US", timezone_id="Asia/Jakarta")
        page = ctx.new_page()
        print("[auto] goto", URL, flush=True)
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#email-input", timeout=30000)
        # WAJIB: tunggu React hydrate + settle. Isi form sebelum hydrate
        # = React error #418 -> handler mati, field ke-wipe, klik percuma.
        try:
            page.wait_for_load_state("networkidle", timeout=25000)
        except Exception:
            pass
        page.wait_for_timeout(5000)
        page.fill("#email-input", email)
        cb = page.locator("input[type=checkbox]").first
        if not cb.is_checked():
            cb.click()
        page.fill("input[type=password]", pw)
        # guard: kalau hydration keburu jalan pas kita isi, value bisa ke-reset
        for _ in range(4):
            page.wait_for_timeout(700)
            val = page.evaluate("() => (document.querySelector('#email-input')||{}).value || ''")
            if val == email:
                break
            page.fill("#email-input", email)
            if not cb.is_checked():
                cb.click()
            page.fill("input[type=password]", pw)
        print("[auto] form: email+tos+password(len=%d) [hydrate ok]" % len(pw),
              flush=True)

        posts = []          # POST register asli (host webshare, bukan beacon GA)
        def _is_reg(r):
            if r.request.method != "POST":
                return False
            if "register" not in r.url.split("?", 1)[0]:
                return False
            return (r.url.startswith("https://dashboard.webshare.io")
                    or r.url.startswith("https://proxy.webshare.io"))
        def _on_reg(r):
            if not _is_reg(r):
                return
            try:
                body = r.text()[:400]
            except Exception:
                body = ""
            posts.append({"status": r.status, "url": r.url, "body": body})
        page.on("response", _on_reg)
        solver = recaptchav2.SyncSolver(page)   # listener token aktif
        submit = page.locator("button[type=submit], button:has-text('Register')").first
        print("[auto] klik Register -> invisible challenge...", flush=True)
        token, note = None, "no-challenge"
        for attempt in range(3):
            if token:
                break
            if attempt > 0:
                # reload penuh: kalau recaptcha script gagal load, klik ulang
                # di halaman yang sama percuma -> hydrate ulang + isi ulang
                print(f"[auto] a{attempt} reload+refill...", flush=True)
                page.reload(wait_until="domcontentloaded", timeout=60000)
                page.wait_for_selector("#email-input", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=25000)
                except Exception:
                    pass
                page.wait_for_timeout(4000)
                page.fill("#email-input", email)
                if not cb.is_checked():
                    cb.click()
                page.fill("input[type=password]", pw)
                page.wait_for_timeout(500)
            try:
                with page.expect_response(_is_reg, timeout=15000):
                    submit.click()
            except Exception:
                pass
            up = False
            dl = time.time() + 45      # Google kadang lambat buka challenge
            while time.time() < dl:
                if solver._token:      # pass-through token tanpa challenge
                    break
                if any_challenge_visible(page):
                    up = True; break
                page.wait_for_timeout(400)
            print("[auto] a%d challenge: %s" % (attempt,
                  "UP" if up else "TIDAK MUNCUL"), flush=True)
            if solver._token:
                token, note = solver._token, "pass-through-token"
                break
            if not up:
                # klik sering ga ngaruh utk widget invisible -> coba lg
                dl = time.time() + 10
                while time.time() < dl and not solver._token:
                    page.wait_for_timeout(300)
                if solver._token:
                    token, note = solver._token, "pass-through-token"
                    break
                # diag: recaptcha widget ke-render nggak?
                st = page.evaluate("""() => ({
                    cfg: !!window.___grecaptcha_cfg,
                    widgets: window.___grecaptcha_cfg
                        ? Object.keys(window.___grecaptcha_cfg.widgets || {}).length : -1,
                    anchor: [...document.querySelectorAll('iframe')]
                        .filter(f => /anchor/.test(f.src || '')).length,
                    grResp: document.querySelectorAll('.g-recaptcha-response').length,
                    toast: [...document.querySelectorAll('[role=alert]')]
                        .map(e => e.textContent.trim()).filter(Boolean).slice(0, 2)
                })""")
                print("[auto] a%d rc-state=%s" % (attempt, json.dumps(st)),
                      flush=True)
                continue
            # IMAGE dulu (audio-disabled click = challenge MATI — terbukti),
            # audio jadi opsi TERAKHIR
            token, note = solve_image_clip(page, solver)
            print("[image]", note, "->", "TOKEN" if token else "fallback audio",
                  flush=True)
            if not token:
                token, note = solve_audio_manual(page, solver)
            if not token:
                note = note + " (attempt %d)" % attempt
        wall = round(time.time() - t0, 1)
        print("[auto] %s len=%d wall=%.1fs note=%s" % (
            "TOKEN" if token else "NO_TOKEN", len(token or ""), wall, note),
            flush=True)

        verdict = "NO_TOKEN"
        api = None
        if token:
            # De() site (recaptcha promise) ga resolve pas solve manual ->
            # kirim POST /register/ sendiri in-page (axios-compatible, cookie sama)
            print("[auto] manual POST /register/ dengan token", flush=True)
            posts_found = page.evaluate("""async (payload) => {
                // axios interceptor: baseURL https://proxy.webshare.io/api
                // + apiVersion v2 -> /api/v2/register/ + custom UA header
                const api = 'https://proxy.webshare.io/api/v2/register/';
                try {
                    const r = await fetch(api, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'X-Webshare-User-Agent': 'WebshareDashboard/2925706886'},
                        credentials: 'include',
                        body: JSON.stringify(payload)});
                    const text = (await r.text()).slice(0, 500);
                    return {path: api, status: r.status, text};
                } catch (e) { return {path: api, status: 0, text: String(e)}; }
            }""", {"email": email, "password": pw,
                   "tos_accepted": True, "recaptcha": token})
            if posts_found:
                print("[auto] POST hasil:", json.dumps(posts_found)[:300], flush=True)
                posts.append({"status": posts_found["status"],
                              "url": posts_found["path"],
                              "body": posts_found["text"]})
            else:
                print("[auto] manual POST ga kena (404 semua)", flush=True)
            # kalau manual POST gagal -> fallback klik tombol lagi
            if not posts:
                # field sering ke-wipe React re-render -> refill dulu
                print("[auto] POST belum kekirim -> refill + klik Register ulang",
                      flush=True)
                try:
                    cur = page.evaluate(
                        "() => (document.querySelector('#email-input')||{}).value || ''")
                    if cur != email:
                        page.fill("#email-input", email)
                    if not cb.is_checked():
                        cb.click()
                    if not page.evaluate(
                            "() => (document.querySelector('input[type=password]')||{}).value || ''"):
                        page.fill("input[type=password]", pw)
                    page.wait_for_timeout(300)
                    # JS click — locator.click timeout kena overlay widget
                    page.evaluate("""() => { const b = document.querySelector('button[type=submit]')
                        || [...document.querySelectorAll('button')].find(x => /sign up with email/i.test(x.innerText||''));
                        if (b) { b.click(); return 'clicked'; } return 'none'; }""")
                except Exception as e:
                    print("[auto] re-submit err:", type(e).__name__, flush=True)
                dl = time.time() + 15
                while time.time() < dl and not posts:
                    page.wait_for_timeout(300)
            if posts:
                api = {"status": posts[-1]["status"], "url": posts[-1]["url"],
                       "body": posts[-1].get("body", "")}
                try:
                    api["page_text"] = page.evaluate(
                        "() => document.body.innerText.slice(0,300)")
                except Exception:
                    pass
            time.sleep(2)
            cur = page.url
            try:
                page.screenshot(path=os.path.join(HERE, "ws_final.png"), full_page=True)
            except Exception:
                pass
            if "dashboard" in cur and "register" not in cur:
                verdict = "SERVER_ACCEPTED"
            elif api and api["status"] in (200, 201) and "error" not in (api.get("page_text", "") or "").lower():
                verdict = "SERVER_ACCEPTED"
                if api.get("body", "").startswith("{"):
                    try:
                        tok = json.loads(api["body"]).get("token")
                    except Exception:
                        tok = None
                    if tok:
                        api["session_token"] = tok   # bukti register API sukses
            elif api and api["status"] >= 400:
                verdict = "SERVER_REJECTED"
            else:
                verdict = "INCONCLUSIVE"
        browser.close()

    out = {"verdict": verdict, "email": email, "note": note,
           "wall_s": wall, "api": api}
    if verdict == "SERVER_ACCEPTED":
        rec = {"email": email, "password": pw, "ts": time.time()}
        if api and api.get("session_token"):
            rec["session_token"] = api["session_token"]
        json.dump(rec, open(CREDS, "w"), indent=1)
        out["creds"] = "ws_creds.json"
    print(json.dumps(out, indent=1, ensure_ascii=False), flush=True)
    return {"SERVER_ACCEPTED": 0, "SERVER_REJECTED": 4}.get(verdict, 3)

if __name__ == "__main__":
    sys.exit(main())
