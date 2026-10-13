#!/usr/bin/env python3
"""tempmail.py — inbox sementara multi-provider utk terima OTP/kode verifikasi.
================================================================================
Dua backend yang dites LIVE 2026-10-09:
  * mail.tm     -> domain aktif cuma maxxspace.com (API: accounts+token)
  * tempmail.plus -> domain mailnu.com / trashmail.com (API publik mailbox)
Catatan: mail.gw sering mati; 1secmail timeout — dihindari.

Pakai:
  python3 tempmail.py new [mailtm|tempplus]        -> buat alamat acak
  python3 tempmail.py wait <alamat> [menit]        -> polling inbox -> cetak OTP
Atau import: from tempmail import TempBox
  b = TempBox("mailtm"); addr = b.address; b.wait_otp(seconds=180)
"""
import json, os, random, re, string, sys, time, urllib.parse, urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0"}


def _http(url, data=None, headers=None, timeout=25, method=None):
    h = dict(UA)
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = json.dumps(data).encode() if isinstance(data, (dict, list)) else data
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode(errors="ignore")


def _rand(n=10):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


class TempBox:
    def __init__(self, backend="mailtm", domain=None):
        assert backend in ("mailtm", "tempplus", "gm", "tml")
        self.b = backend
        self.token = None
        if backend == "mailtm":
            doms = json.loads(_http("https://api.mail.tm/domains")[1])["hydra:member"]
            domain = domain or (doms[0]["domain"] if doms else "maxxspace.com")
            self.address = _rand() + "@" + domain
            self.password = "Tm!" + _rand(12)
            st, body = 0, ""
            for _att in range(4):
                st, body = _http("https://api.mail.tm/accounts",
                                 {"address": self.address, "password": self.password})
                if st == 429:
                    time.sleep(2.0 * (_att + 1))
                    continue
                break
            if st >= 400:
                raise RuntimeError(f"mailtm account {st}: {body[:150]}")
            st, body = _http("https://api.mail.tm/token",
                             {"address": self.address, "password": self.password})
            self.token = json.loads(body)["token"]
        elif backend == "tempplus":
            domain = domain or "mailnu.com"
            self.address = _rand() + "@" + domain
            self.password = None
        elif backend == "gm":
            # guerrillamail: alamat acak dari server, session sid_token
            q = urllib.parse.urlencode({"f": "get_address",
                "ip": "203.0.113.7", "agent": "Mozilla/5.0"})
            st, body = _http("https://api.guerrillamail.com/ajax.php?" + q)
            d = json.loads(body)
            self.address = d.get("email_addr") or d.get("alias")
            self.sid = d.get("sid_token") or d.get("token")
            if not self.address:
                raise RuntimeError("gm addr fail: " + body[:150])
            self.password = None
        elif backend == "tml":
            # tempmail.lol: generate -> {address, token}; domain acak per inbox
            st, body = _http("https://api.tempmail.lol/generate")
            d = json.loads(body)
            self.address = d["address"]
            self.tml_token = d["token"]
            self.password = None
        # sanity: mailbox bisa dibuka
        self._list()
        # persistensi kredensial inbox (biar bisa dibaca ulang setelah process keluar)
        try:
            recs = {}
            p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tempmail_accounts.json")
            if os.path.exists(p):
                recs = json.load(open(p))
            recs[self.address] = {"backend": self.b, "password": self.password,
                                  "token": getattr(self, "token", None),
                                  "tml_token": getattr(self, "tml_token", None),
                                  "created": __import__("time").time()}
            json.dump(recs, open(p, "w"), indent=1)
        except Exception:
            pass

    def _list(self):
        if self.b == "mailtm":
            st, body = _http("https://api.mail.tm/messages?page=1",
                             headers={"Authorization": "Bearer " + self.token})
            if st != 200:
                raise RuntimeError(f"mailtm inbox {st}")
            return [m for m in json.loads(body)["hydra:member"]]
        if self.b == "gm":
            q = urllib.parse.urlencode({"f": "get_email_list", "offset": 0,
                                        "sid_token": self.sid})
            st, body = _http("https://api.guerrillamail.com/ajax.php?" + q)
            out = []
            for m in (json.loads(body).get("list") or []):
                out.append({"subject": m.get("mail_subject", ""),
                            "from": m.get("mail_from", ""),
                            "text": m.get("mail_excerpt", "")})
            return out
        if self.b == "tml":
            st, body = _http("https://api.tempmail.lol/auth/" + self.tml_token)
            out = []
            for m in (json.loads(body).get("emails") or []):
                out.append({"subject": m.get("subject", ""),
                            "from": m.get("from", ""),
                            "text": m.get("body", "")})
            return out
        user = self.address.split("@")[0]
        q = urllib.parse.urlencode({"email": user, "limit": 25})
        st, body = _http(f"https://tempmail.plus/api/mails?{q}")
        if st != 200:
            raise RuntimeError(f"tempplus inbox {st}")
        d = json.loads(body)
        out = []
        for m in d.get("mail_list") or []:
            subj = m.get("subject", "")
            from_addr = m.get("from", "")
            mid = m.get("mail_id")
            body_txt = ""
            if mid:
                try:
                    st2, b2 = _http(f"https://tempmail.plus/api/mails/{mid}?email={user}")
                    if st2 == 200:
                        d2 = json.loads(b2)
                        body_txt = d2.get("text", "") or d2.get("html", "")
                except Exception:
                    pass
            out.append({"subject": subj, "from": from_addr, "text": body_txt})
        return out

    def wait_otp(self, seconds=240, poll=6, subject_kw=None, from_kw=None):
        """Poll inbox, balikin kode pertama yg ketemu. None kalau waktu habis."""
        t_end = time.time() + seconds
        seen = set()
        while time.time() < t_end:
            try:
                msgs = self._list()
            except Exception:
                msgs = []
            for m in msgs:
                key = (m.get("subject"), m.get("from"))
                if key in seen:
                    continue
                seen.add(key)
                blob = (m.get("subject") or "") + "\n" + (m.get("text") or "")
                if subject_kw and subject_kw.lower() not in blob.lower():
                    continue
                if from_kw and from_kw.lower() not in blob.lower():
                    continue
                codes = re.findall(r"\b(\d{4,8})\b", blob)
                if codes:
                    return codes[0]
                # magic-link / code panjang
                link = re.search(r"https?://\S+", blob)
                if link and ("verify" in blob.lower() or "confirm" in blob.lower()
                             or "code" in blob.lower()):
                    return link.group(0)
            time.sleep(poll)
        return None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "new"
    backend = sys.argv[2] if len(sys.argv) > 1 and cmd == "new" else "mailtm"
    if cmd == "new":
        b = TempBox(backend)
        print(json.dumps({"address": b.address, "backend": b.b}))
    elif cmd == "wait":
        addr, mins = sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 4
        backend = "mailtm" if addr.endswith("maxxspace.com") else "tempplus"
        b = TempBox(backend)
        b.address = addr
        otp = b.wait_otp(seconds=int(mins * 60))
        print(json.dumps({"otp": otp, "address": addr}))
    else:
        print(__doc__)
