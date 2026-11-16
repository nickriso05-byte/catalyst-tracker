"""
Catalyst Tracker data builder -> public/data.json

What it does
------------
1. Finds upcoming FDA / biotech catalysts in companies' own SEC filings (8-K, 10-Q, 10-K,
   6-K, 20-F) via EDGAR full-text search: PDUFA dates, advisory committees, data readouts,
   regulatory submissions. Each event keeps the sentence it came from as a source quote.
2. Adds forward-dated entries from the Unusual Whales FDA calendar (press-release based).
   That feed lagged about 4 weeks in Nov 2026, so SEC filings are the primary source.
3. Keeps Healthcare-sector names only and enriches each one with:
   price, market cap, short interest (FINRA, twice monthly), insider open-market trades,
   cash runway, institutional holders, 13D/13G stakes, dilution filings,
   and the options-implied move into the catalyst.

Run:   python build.py            (full build, a few minutes; SEC documents are cached)
       python build.py --quick    (caps the SEC document fetch for a fast test run)

Credentials: UW_API_TOKEN env var, or .secrets.toml next to this file, or the
momentum_screener secrets file. SEC_USER_AGENT env var should hold a real contact,
per SEC fair-access rules (a generic one is used if unset).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import threading
import time
import tomllib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).parent
OUT = HERE / "public" / "data.json"
CACHE = HERE / "cache"
CACHE.mkdir(exist_ok=True)
(CACHE / "docs").mkdir(exist_ok=True)

TODAY = date.today()
UW = "https://api.unusualwhales.com"
SEC_UA = os.environ.get("SEC_USER_AGENT", "CatalystTracker research admin@example.com")

# --------------------------------------------------------------------------- credentials

def uw_token() -> str:
    if os.environ.get("UW_API_TOKEN"):
        return os.environ["UW_API_TOKEN"]
    for p in (HERE / ".secrets.toml", HERE.parent / "momentum_screener" / ".streamlit" / "secrets.toml"):
        try:
            return tomllib.load(open(p, "rb"))["UW_API_TOKEN"]
        except Exception:
            continue
    raise SystemExit("No UW_API_TOKEN found (env var or .secrets.toml).")


# --------------------------------------------------------------------------- HTTP helpers

class RateLimiter:
    def __init__(self, per_sec: float):
        self.gap = 1.0 / per_sec
        self.lock = threading.Lock()
        self.next = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            if now < self.next:
                time.sleep(self.next - now)
            self.next = max(now, self.next) + self.gap


SEC_RL = RateLimiter(7)      # SEC allows 10/s in total
UW_RL = RateLimiter(7)
# Live card lookups get their own budget, so a running refresh never makes a click wait.
SEC_RL_LIVE = RateLimiter(3)
UW_RL_LIVE = RateLimiter(4)
_tl = threading.local()
_sec = requests.Session(); _sec.headers.update({"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"})
_uw = requests.Session()


(CACHE / "http").mkdir(exist_ok=True)


def _ckey(*parts) -> Path:
    import hashlib
    return CACHE / "http" / (hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest() + ".json")


def cached(ttl_hours: float | None, key: tuple, fetch):
    """Disk cache for slow-changing responses. ttl_hours=None means no caching; 0 < ttl means max age."""
    if not ttl_hours:
        return fetch()
    p = _ckey(*key)
    if p.exists():
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
            if time.time() - blob["t"] < ttl_hours * 3600:
                return blob["v"]
        except Exception:
            pass
    v = fetch()
    if v is not None:
        p.write_text(json.dumps({"t": time.time(), "v": v}), encoding="utf-8")
    return v


def sec_get(url: str, params: dict | None = None, as_json=False, tries=4, ttl=None):
    return cached(ttl, ("sec", url, params, as_json), lambda: _sec_get(url, params, as_json, tries))


def uw_get(path: str, params: dict | None = None, tries=4, ttl=None):
    return cached(ttl, ("uw", path, params), lambda: _uw_get(path, params, tries))


def _sec_get(url: str, params: dict | None = None, as_json=False, tries=4):
    for i in range(tries):
        (SEC_RL_LIVE if getattr(_tl, 'live', False) else SEC_RL).wait()
        try:
            r = _sec.get(url, params=params, timeout=60)
            if r.status_code == 200:
                if as_json:
                    return r.json()
                try:
                    return r.content.decode("utf-8")
                except UnicodeDecodeError:
                    return r.content.decode("cp1252", errors="replace")
            if r.status_code in (429, 503):
                time.sleep(2 + 3 * i); continue
            return None
        except requests.RequestException:
            time.sleep(1 + i)
    return None


def _uw_get(path: str, params: dict | None = None, tries=4):
    for i in range(tries):
        (UW_RL_LIVE if getattr(_tl, 'live', False) else UW_RL).wait()
        try:
            r = _uw.get(UW + path, params=params, timeout=60)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(3 + 4 * i); continue
            return None
        except requests.RequestException:
            time.sleep(1 + i)
    return None


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- date extraction

MON = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"], 1)}
MON.update({"jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12})
MRX = r"(?P<m>january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
ORD = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4}
MNAME = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _y(s: str) -> int:
    y = int(s)
    return y + 2000 if y < 100 else y


def _mend(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def _win(y, m1, m2, precision, label):
    return {"start": date(y, m1, 1), "end": _mend(y, m2), "precision": precision, "label": label}


DATE_PATTERNS = [
    # exact day
    (re.compile(MRX + r"\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?,?\s+(?P<y>20\d\d)\b", re.I),
     lambda g: (lambda y, m, d: {"start": date(y, m, d), "end": date(y, m, d), "precision": "day",
                                 "label": f"{MNAME[m]} {d}, {y}"})(int(g["y"]), MON[g["m"].lower()], int(g["d"]))),
    # quarter
    (re.compile(r"\b(?P<q>first|second|third|fourth|1st|2nd|3rd|4th)[\s-]+(?:calendar\s+)?quarter(?:\s+of)?\s+(?P<y>20\d\d)\b", re.I),
     lambda g: (lambda y, q: _win(y, 3 * q - 2, 3 * q, "quarter", f"Q{q} {y}"))(int(g["y"]), ORD[g["q"].lower()])),
    (re.compile(r"\b(?:Q(?P<q>[1-4])|(?P<q2>[1-4])Q)[\s'’-]*(?P<y>20\d\d|\d\d)\b"),
     lambda g: (lambda y, q: _win(y, 3 * q - 2, 3 * q, "quarter", f"Q{q} {y}"))(_y(g["y"]), int(g["q"] or g["q2"]))),
    # half
    (re.compile(r"\b(?P<h>first|second|1st|2nd)[\s-]+half(?:\s+of)?\s+(?P<y>20\d\d)\b", re.I),
     lambda g: (lambda y, h: _win(y, 1 if h == 1 else 7, 6 if h == 1 else 12, "half", f"H{h} {y}"))(int(g["y"]), ORD[g["h"].lower()])),
    (re.compile(r"\b(?:H(?P<h>[12])|(?P<h2>[12])H)[\s'’-]*(?P<y>20\d\d|\d\d)\b"),
     lambda g: (lambda y, h: _win(y, 1 if h == 1 else 7, 6 if h == 1 else 12, "half", f"H{h} {y}"))(_y(g["y"]), int(g["h"] or g["h2"]))),
    # month (optionally early/mid/late)
    (re.compile(r"\b(?:(?P<pre>early|mid|late|end of|the end of)[\s-]+)?" + MRX + r",?\s+(?P<y>20\d\d)\b", re.I),
     lambda g: (lambda y, m: _win(y, m, m, "month", f"{MNAME[m]} {y}"))(int(g["y"]), MON[g["m"].lower()])),
    # fuzzy parts of a year
    (re.compile(r"\b(?:mid|middle of)[\s-]+(?P<y>20\d\d)\b", re.I), lambda g: _win(int(g["y"]), 4, 9, "half", f"Mid-{g['y']}")),
    (re.compile(r"\b(?:early|beginning of|start of)[\s-]+(?P<y>20\d\d)\b", re.I), lambda g: _win(int(g["y"]), 1, 4, "half", f"Early {g['y']}")),
    (re.compile(r"\b(?:late|end of|year[\s-]end)[\s-]+(?P<y>20\d\d)\b", re.I), lambda g: _win(int(g["y"]), 9, 12, "half", f"Late {g['y']}")),
    # bare year
    (re.compile(r"\b(?:in|during|by|within)\s+(?:calendar\s+)?(?P<y>20\d\d)\b", re.I), lambda g: _win(int(g["y"]), 1, 12, "year", g["y"])),
]


def find_dates(s: str):
    """All date mentions in a sentence, most precise first, without overlaps."""
    taken, out = [], []
    for rx, fn in DATE_PATTERNS:
        for m in rx.finditer(s):
            a, b = m.span()
            if any(a < tb and b > ta for ta, tb in taken):
                continue
            try:
                d = fn(m.groupdict())
            except (ValueError, KeyError):
                continue
            taken.append((a, b))
            d["pos"] = a
            out.append(d)
    return out


# --------------------------------------------------------------------------- catalyst sentences

TYPES = [  # (type, sentence regex, needs forward-looking verb)
    ("PDUFA date", re.compile(r"\bPDUFA\b|target action date|goal date", re.I), False),
    ("Advisory committee", re.compile(r"advisory committee meeting|\bAdCom\b|advisory committee (?:is|has been) scheduled", re.I), False),
    ("EU decision", re.compile(r"\bCHMP\b.*\bopinion|European Commission decision", re.I), True),
    ("Regulatory submission", re.compile(r"\b(?:submit|submission of|file|filing of)\s+(?:an?\s+|the\s+|our\s+)?(?:s?NDA|s?BLA|MAA|PMA|510\(k\)|De Novo)\b", re.I), True),
    ("Data readout", re.compile(r"\btop[\s-]?line\b|\breadout\b|\binterim (?:data|analysis|results)\b|\b(?:data|results) (?:are|is) (?:expected|anticipated)\b", re.I), True),
]
FORWARD = re.compile(r"\b(?:expect|anticipat|plan|on track|scheduled|guid|intend|target|will|remain)", re.I)
KEYPOS = re.compile(r"PDUFA|target action date|goal date|advisory committee|top[\s-]?line|readout|interim|submi|CHMP|data|results", re.I)

DRUG_CODE = re.compile(r"\b(?!COVID-|EX-|FORM|ITEM|RULE|SECTION|NASDAQ|NYSE|CIK|CUSIP|IL-|HPV-|CD-|ICD-|ISO-|GAAP)[A-Z]{2,6}-\d{2,6}[A-Za-z]?\b")
DRUG_INN = re.compile(r"\b[a-z]{3,}(?:mab|nib|cel|stat|stim|tide|vir|parin|gene|sertib|ciclib|lisib|zomib|platin|rsen|siran|cept|glutide|tecan|leucel|temcel|vec|dagene|vimab|ximab|zumab|umab|lintide|igine|azine|olol|pril|sartan|fungin|cycline|mycin|oxacin|dronate|pam|lam|zolam|tinib|rafenib|parib|degib|lukast|semide|thiazide|bart|plirsen|virsen|amide)\b", re.I)
STOP_INN = {"concept", "except", "accept", "intercept", "precept", "program", "random", "medicine", "examine", "determine", "statement", "outcome", "become", "data", "submitted"}


DRUG_SHORT = re.compile(r"\b[a-z]{2,6}-(?:cel|vec|gal|cept)\b")   # tab-cel, bota-vec, …


def guess_drug(s: str, at: int = 0):
    """Drug code (ABC-123) or generic name (…mab, …nib) closest to the catalyst keyword."""
    cands = [(m.start(), m.group(0)) for m in DRUG_CODE.finditer(s)]
    cands += [(m.start(), m.group(0)) for m in DRUG_SHORT.finditer(s)]
    cands += [(m.start(), m.group(0).lower()) for m in DRUG_INN.finditer(s) if m.group(0).lower() not in STOP_INN]
    if not cands:
        return None
    return min(cands, key=lambda c: abs(c[0] - at))[1]


BOILER = re.compile(r"not necessarily indicative|year(?:s)? ending|months ended|fiscal year|forward-looking statements? (?:include|in this)|"
                    r"risk factors|could cause actual results|safe harbor", re.I)


def trim_quote(s: str, a: int, b: int, width: int = 360) -> str:
    """A readable window of the sentence around the keyword (a) and the date (b)."""
    s = s.strip()
    if len(s) <= width:
        return s
    lo, hi = min(a, b), max(a, b)
    start = max(0, lo - (width - (hi - lo)) // 2)
    end = min(len(s), start + max(width, hi - lo + 40))
    while start > 0 and s[start - 1] not in " •▪": start -= 1
    while end < len(s) and s[end] not in " •▪": end += 1
    return ("…" if start > 0 else "") + s[start:end].strip() + ("…" if end < len(s) else "")


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|ix:header).*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw).replace("\xa0", " ")
    return re.sub(r"\s+", " ", raw)


SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(“\"•])")


# filing-wrapper noise at the top of exhibits: "EX-99.1 2 capr-ex99d1.htm EX-99.1 Exhibit 99.1"
EXHDR = re.compile(r"^\s*(?:\bEX-\d+(?:\.\d+)?\s+\d+\s+\S+\.htm\s+)?(?:\bEX(?:HIBIT)?-?\s?\d+(?:\.\d+)?\s+)+(?:Exhibit\s+\d+(?:\.\d+)?\s+)?", re.I)

ANYKEY = re.compile("|".join(rx.pattern for _, rx, _ in TYPES), re.I)


def candidate_sentences(text: str):
    return [s for s in SPLIT.split(text) if len(s) >= 30 and ANYKEY.search(s)]


def extract_events(sentences, filed: str | None = None):
    """Catalyst events in a filing. Dates within 3 days of the filing date are datelines, not catalysts."""
    fd = date.fromisoformat(filed) if filed else None
    events = []
    for sent in sentences:
        sent = EXHDR.sub(" ", sent).strip()
        if len(sent) < 30:
            continue
        for etype, rx, need_fwd in TYPES:
            s = sent
            km = rx.search(s)
            if not km:
                continue
            if len(s) > 700:   # tables / run-ons: keep a window around the keyword
                a = max(0, km.start() - 300); s = s[a:a + 600]
                km = rx.search(s)
                if not km:
                    continue
            if BOILER.search(s):
                continue
            if need_fwd and not FORWARD.search(s[max(0, km.start() - 150): km.end() + 150]):
                continue
            dates = [d for d in find_dates(s) if d["end"] >= TODAY and d["start"] <= TODAY + timedelta(days=760)
                     and not (fd and d["precision"] == "day" and abs((d["start"] - fd).days) <= 3)]
            if not dates:
                continue
            # nearest date to the keyword; ties go to the date after it
            pick = min(dates, key=lambda d: (abs(d["pos"] - km.start()) if d["pos"] >= km.start() else abs(d["pos"] - km.start()) + 40))
            if etype == "PDUFA date" and pick["precision"] == "year":
                continue
            events.append({"type": etype, "label": pick["label"], "start": pick["start"].isoformat(),
                           "end": pick["end"].isoformat(), "precision": pick["precision"],
                           "drug": guess_drug(s, km.start()), "quote": trim_quote(s, km.start(), pick["pos"])})
    return events


# --------------------------------------------------------------------------- SEC: find filings

FTS_QUERIES = ['"PDUFA"', '"target action date"', '"topline data"', '"top-line data"', '"topline results"',
               '"advisory committee meeting"', '"BLA submission"', '"NDA submission"']
FTS_FORMS = "8-K,10-Q,10-K,6-K,20-F"


def fts_hits(days: int, max_per_query: int):
    start = (TODAY - timedelta(days=days)).isoformat()
    hits = {}
    for q in FTS_QUERIES:
        off = 0
        while off < max_per_query:
            j = sec_get("https://efts.sec.gov/LATEST/search-index", {"q": q, "forms": FTS_FORMS, "dateRange": "custom",
                         "startdt": start, "enddt": TODAY.isoformat(), "from": off}, as_json=True)
            if not j:
                break
            page = j.get("hits", {}).get("hits", [])
            for h in page:
                s = h["_source"]
                names = s.get("display_names") or []
                tk = None
                for n in names:
                    m = re.search(r"\(([A-Z][A-Z0-9.\-]{0,6})(?:,|\))", n)
                    if m:
                        tk = m.group(1); break
                if not tk:
                    continue
                hits[h["_id"]] = {"id": h["_id"], "ticker": tk, "cik": int((s.get("ciks") or ["0"])[0]),
                                  "form": s.get("form") or s.get("root_forms", [""])[0], "date": s.get("file_date"),
                                  "company": re.sub(r"\s*\(.*$", "", names[0]).strip()}
            total = j.get("hits", {}).get("total", {}).get("value", 0)
            off += 100
            if off >= total or not page:
                break
        print(f"  search {q:<26} total so far {len(hits)}")
    return list(hits.values())


def doc_url(hit):
    adsh, fn = hit["id"].split(":", 1)
    return f"https://www.sec.gov/Archives/edgar/data/{hit['cik']}/{adsh.replace('-', '')}/{fn}"


def load_doc_events(hit):
    """Cache only the keyword sentences of each filing, so extraction rules can change without re-downloading."""
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", hit["id"]) + ".json"
    p = CACHE / "docs" / key
    if p.exists():
        sents = json.loads(p.read_text(encoding="utf-8"))
    else:
        raw = sec_get(doc_url(hit))
        if raw is None:
            return None
        sents = candidate_sentences(html_to_text(raw))
        p.write_text(json.dumps(sents), encoding="utf-8")
    return extract_events(sents, hit["date"])


# --------------------------------------------------------------------------- UW FDA calendar

UW_TYPE = [(re.compile(r"pdufa", re.I), "PDUFA date"), (re.compile(r"advisory|adcom", re.I), "Advisory committee"),
           (re.compile(r"submission|nda|bla", re.I), "Regulatory submission"),
           (re.compile(r"top-?line|data|results|readout|primary endpoint", re.I), "Data readout")]


def uw_target(t: str):
    if not t:
        return None
    t = t.strip().upper()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", t):
            d = date.fromisoformat(t); return {"start": d, "end": d, "precision": "day", "label": f"{MNAME[d.month]} {d.day}, {d.year}"}
        y = int(t[:4]); rest = t[5:]
        if re.fullmatch(r"Q[1-4]", rest):
            q = int(rest[1]); return _win(y, 3 * q - 2, 3 * q, "quarter", f"Q{q} {y}")
        if re.fullmatch(r"H[12]", rest):
            h = int(rest[1]); return _win(y, 1 if h == 1 else 7, 6 if h == 1 else 12, "half", f"H{h} {y}")
        if rest.startswith("MID"):
            return _win(y, 4, 9, "half", f"Mid-{y}")
        if rest.startswith("LA"):
            return _win(y, 9, 12, "half", f"Late {y}")
        if re.fullmatch(r"\d{2}", rest):
            m = int(rest); return _win(y, m, m, "month", f"{MNAME[m]} {y}")
    except ValueError:
        return None
    return None


def uw_fda_events():
    j = uw_get("/api/market/fda-calendar", {"target_date_min": (TODAY - timedelta(days=31)).isoformat()[:7] + "-01",
                                            "target_date_max": (TODAY + timedelta(days=760)).isoformat(), "limit": 500}, ttl=6)
    rows = (j or {}).get("data", [])
    latest = {}
    for r in rows:
        tk = (r.get("ticker") or "").strip()
        etype = next((ty for rx, ty in UW_TYPE if rx.search(r.get("catalyst") or "")), None)
        w = uw_target(r.get("target_date"))
        if not tk or not etype or not w or w["end"] < TODAY:
            continue
        k = (tk, (r.get("drug") or "").lower(), etype)
        if k not in latest or (r.get("created_at") or "") > (latest[k][0].get("created_at") or ""):
            latest[k] = (r, etype, w)
    out = []
    for r, etype, w in latest.values():
        out.append({"ticker": r["ticker"].strip(), "type": etype, "label": w["label"], "start": w["start"].isoformat(),
                    "end": w["end"].isoformat(), "precision": w["precision"], "drug": (r.get("drug") or "").strip() or None,
                    "indication": (r.get("indication") or "").strip() or None, "quote": (r.get("outcome") or "").strip()[:600],
                    "sources": [{"kind": "Press release", "date": (r.get("created_at") or "")[:10], "url": r.get("source_link")}]})
    print(f"  UW FDA calendar: {len(rows)} rows -> {len(out)} forward events")
    return out


# --------------------------------------------------------------------------- merge events

PREC_RANK = {"day": 0, "month": 1, "quarter": 2, "half": 3, "year": 4}


def merge_events(sec_raw, uw_events):
    """sec_raw: list of (hit, event). Keep, per ticker+type+drug, what the newest filing says."""
    groups = {}
    for hit, ev in sec_raw:
        k = (hit["ticker"], ev["type"], (ev["drug"] or "?").lower())
        groups.setdefault(k, []).append((hit, ev))
    out = []
    for (tk, etype, drug), items in groups.items():
        items.sort(key=lambda x: x[0]["date"] or "", reverse=True)
        newest = items[0][0]["date"]
        cur = [x for x in items if x[0]["date"] == newest]
        seen = {}
        for hit, ev in cur:
            k = ev["label"]
            src = {"kind": hit["form"], "date": hit["date"], "url": doc_url(hit)}
            if k in seen:
                if src["url"] not in [s["url"] for s in seen[k]["sources"]]:
                    seen[k]["sources"].append(src)
                continue
            seen[k] = {**ev, "ticker": tk, "company": hit["company"], "cik": hit["cik"], "sources": [src]}
        cur_events = list(seen.values())
        # Date changed vs an older filing? (only when the newest filing gives one date)
        # Only trusted when the drug is known and the old date is close (a lease or loan date isn't a moved PDUFA).
        if len(cur_events) == 1 and drug != "?":
            c0 = cur_events[0]
            older = [ev for h, ev in items if h["date"] < newest and ev["label"] != c0["label"]
                     and PREC_RANK[ev["precision"]] <= PREC_RANK[c0["precision"]]
                     and abs((date.fromisoformat(ev["start"]) - date.fromisoformat(c0["start"])).days) <= 270
                     and (ev["end"] < c0["start"] or ev["start"] > c0["end"])]   # a real change, not "Late 2026" vs "H2 2026"
            if older:
                prev = older[0]
                cur_events[0]["was"] = prev["label"]
                cur_events[0]["moved"] = "later" if prev["end"] < cur_events[0]["end"] else "earlier"
        out.extend(cur_events)
    # Add UW calendar events where the filings didn't already cover that ticker+type in an overlapping window
    for u in uw_events:
        dup = None
        for e in out:
            if e["ticker"] == u["ticker"] and e["type"] == u["type"] and not (e["end"] < u["start"] or u["end"] < e["start"]):
                dup = e; break
        if dup:
            dup.setdefault("indication", u.get("indication"))
            if not dup.get("drug") and u.get("drug"):
                dup["drug"] = u["drug"]
            dup["sources"].extend(u["sources"])
        else:
            out.append(u)
    # Drop duplicates: same ticker+type, same (or unknown) drug, overlapping window -> keep the most precise,
    # preferring the one that names the drug, and pool their sources.
    def same_drug(a, b):
        da, db = (a.get("drug") or "").lower(), (b.get("drug") or "").lower()
        return not da or not db or da == db
    out.sort(key=lambda e: (PREC_RANK[e["precision"]], 0 if e.get("drug") else 1))
    kept = []
    for e in out:
        twin = next((k for k in kept if k["ticker"] == e["ticker"] and k["type"] == e["type"] and same_drug(k, e)
                     and not (k["end"] < e["start"] or e["end"] < k["start"])), None)
        if twin:
            for s in e["sources"]:
                if s.get("url") not in [x.get("url") for x in twin["sources"]]:
                    twin["sources"].append(s)
            if not twin.get("indication") and e.get("indication"):
                twin["indication"] = e["indication"]
            continue
        kept.append(e)
    # An "old" date that is another of the company's current events belongs to that other program, not a move.
    for e in kept:
        if e.get("was") and any(k is not e and k["ticker"] == e["ticker"] and k["label"] == e["was"] for k in kept):
            e.pop("was", None); e.pop("moved", None)
    kept.sort(key=lambda e: (e["end"], PREC_RANK[e["precision"]], e["label"], e["start"], e["ticker"]))
    return kept


# --------------------------------------------------------------------------- per-ticker enrichment

SPECIALISTS = ["baker bros", "ra capital", "perceptive", "deerfield", "orbimed", "avoro", "venrock", "ecor1", "cormorant",
               "biotechnology value", "bvf ", "vivo capital", "commodore", "logos global", "ally bridge", "great point",
               "boxer capital", "fairmount", "frazier", "bain capital life", "samsara", "tcg crossover", "suvretta",
               "rock springs", "janus henderson", "redmile", "farallon", "armistice", "stonepine", "affinity asset",
               "acuta", "eventide", "polar capital", "candriam", "rtw investments", "paradigm biocapital", "adage capital",
               "soleus", "sphera", "aisling", "omega fund", "5am", "atlas venture", "third rock", "foresite", "decheng",
               "octagon", "kynam", "deep track", "siren", "nextech", "sio capital", "rp management", "privet", "ikarian",
               "monashee", "tang capital", "two seas", "ptolemy", "abingworth"]


def is_specialist(name: str) -> bool:
    n = (name or "").lower()
    return any(s in n for s in SPECIALISTS)


def runway(fin):
    bs = sorted([b for b in (fin or {}).get("balance_sheets", []) if b.get("report_type") == "quarterly"],
                key=lambda b: b["fiscal_date_ending"], reverse=True)
    cf = sorted([c for c in (fin or {}).get("cash_flows", []) if c.get("report_type") == "quarterly"],
                key=lambda c: c["fiscal_date_ending"], reverse=True)
    if not bs:
        return None
    b = bs[0]
    cash = fnum(b.get("cash_and_cash_equivalents")) or 0
    st = fnum(b.get("short_term_investments")) or 0
    csti = fnum(b.get("cash_and_short_term_investments")) or 0
    liquid = max(csti, cash + st)
    ocf = [fnum(c.get("operating_cashflow")) for c in cf[:2] if fnum(c.get("operating_cashflow")) is not None]
    res = {"as_of": b["fiscal_date_ending"], "cash": liquid, "debt": fnum(b.get("short_long_term_debt_total")),
           "qtr_burn": None, "months": None, "runs_out": None}
    if ocf:
        burn = -sum(ocf) / len(ocf)
        res["qtr_burn"] = burn
        if burn > 0:
            m = min(liquid / (burn / 3), 1200.0)   # tiny burn: cap at 100 years
            asof = date.fromisoformat(b["fiscal_date_ending"])
            out_date = asof + timedelta(days=int(m * 30.44))
            res["months"] = round(max(0.0, (out_date - TODAY).days / 30.44), 1)
            res["runs_out"] = out_date.isoformat()
    return res


DILUTION_FORMS = {"S-3", "S-3ASR", "S-3/A", "S-1", "S-1/A", "424B5", "424B4", "424B3", "424B2", "F-3", "F-1", "F-3ASR"}


def sec_stakes(cik: int, ttl=6):
    s = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", as_json=True, ttl=ttl)
    if not s:
        return [], []
    rec = s["filings"]["recent"]
    stakes, dil = [], []
    cutoff = (TODAY - timedelta(days=365)).isoformat()
    dcut = (TODAY - timedelta(days=180)).isoformat()
    for i, form in enumerate(rec["form"]):
        fd = rec["filingDate"][i]
        adsh = rec["accessionNumber"][i]
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}"
        if form.startswith("SCHEDULE 13") and fd >= cutoff and len(stakes) < 8:
            stakes.append({"form": form.replace("SCHEDULE ", ""), "date": fd, "url": f"{base}/{rec['primaryDocument'][i]}", "xml": f"{base}/primary_doc.xml"})
        elif form in DILUTION_FORMS and fd >= dcut:
            dil.append({"form": form, "date": fd, "url": f"{base}/{rec['primaryDocument'][i]}"})
    for st in stakes:
        x = sec_get(st.pop("xml"), ttl=24 * 365)
        st.update(parse_13x(x) if x else {})
    return stakes, dil[:8]


def parse_13x(xml: str):
    try:
        root = ET.fromstring(xml.encode("utf-8"))
    except ET.ParseError:
        return {}
    def local(t): return t.rsplit("}", 1)[-1]
    people = []
    for el in root.iter():
        if local(el.tag) in ("coverPageHeaderReportingPersonDetails", "reportingPersonInfo"):
            d = {local(c.tag): (c.text or "").strip() for c in el.iter()}
            name = d.get("reportingPersonName") or d.get("reportingPersonNoCIK") or ""
            pct = fnum(d.get("classPercent") or d.get("percentOfClass"))
            sh = fnum(d.get("reportingPersonBeneficiallyOwnedAggregateNumberOfShares") or d.get("aggregateAmountOwned"))
            people.append((name, pct, sh))
    if not people:
        return {}
    best = max(people, key=lambda p: (p[1] or 0))
    return {"holder": people[0][0].title() if people[0][0].isupper() else people[0][0], "pct": best[1], "shares": best[2],
            "specialist": any(is_specialist(p[0]) for p in people)}


def pick_expiry(expiries, cat_date: date):
    cand = sorted(e for e in expiries if e >= cat_date.isoformat())
    return cand[0] if cand else None


def enrich(tk: str, cik: int | None, next_exact: str | None, fresh: bool = False):
    """fresh=True: re-pull the fast-moving parts (price, insiders, filings, options) instead of using the cache."""
    fast = (lambda h: 0.05) if fresh else (lambda h: h)
    info = (uw_get(f"/api/stock/{tk}/info", ttl=24) or {}).get("data") or {}
    if not info or (info.get("sector") or "") != "Healthcare":
        return None
    r = {"name": (info.get("full_name") or "").title(), "sector": info.get("sector"), "mcap": fnum(info.get("marketcap")),
         "shares_out": fnum(info.get("outstanding")), "avg_vol": fnum(info.get("avg30_volume")),
         "has_options": bool(info.get("has_options")), "next_earnings": info.get("next_earnings_date")}
    # price + 1-month change
    o = (uw_get(f"/api/stock/{tk}/ohlc/1d", {"timeframe": "3M"}, ttl=fast(4)) or {}).get("data") or []
    o = sorted(o, key=lambda x: x.get("date") or x.get("start_time") or "")
    if o:
        last = fnum(o[-1].get("close")); r["price"] = last
        if len(o) > 21 and fnum(o[-22].get("close")):
            r["chg_1m"] = last / fnum(o[-22]["close"]) - 1
        r["spark"] = [round(fnum(x.get("close")) or 0, 4) for x in o[-63:]]
    # short interest
    sh = (uw_get(f"/api/shorts/{tk}/interest-float/v2", ttl=12) or {}).get("data") or []
    sh = sorted(sh, key=lambda x: x.get("market_date") or "", reverse=True)
    if sh:
        s0 = sh[0]
        r["short"] = {"as_of": s0.get("market_date"), "si_float": fnum(s0.get("si_float")), "dtc": fnum(s0.get("days_to_cover")),
                      "fee": fnum(s0.get("fee_rate")), "available": s0.get("short_shares_available"),
                      "si_float_prev": fnum(sh[3].get("si_float")) if len(sh) > 3 else None,
                      "history": [[x.get("market_date"), fnum(x.get("si_float"))] for x in sh[:12]][::-1]}
    # insiders: open-market buys (P) and sells (S) only, last 12 months
    ins = (uw_get("/api/insider/transactions", {"ticker_symbol": tk, "limit": 200, "start_date": (TODAY - timedelta(days=365)).isoformat(),
                                               "form_types[]": "4"}, ttl=fast(6)) or {}).get("data") or []
    trades = []
    for t in ins:
        code = t.get("transaction_code")
        if code not in ("P", "S") or "common" not in (t.get("security_title") or "common").lower():
            continue
        amt, px = fnum(t.get("amount")) or 0, fnum(t.get("price")) or 0
        role = "10% owner" if t.get("is_ten_percent_owner") else (t.get("officer_title") or ("Director" if t.get("is_director") else "Insider"))
        trades.append({"date": t.get("transaction_date"), "filed": t.get("filing_date"), "who": (t.get("owner_name") or "").title(),
                       "role": (role or "").title(), "code": code, "shares": abs(amt), "price": px, "value": abs(amt) * px,
                       "planned": bool(t.get("is_10b5_1"))})
    trades.sort(key=lambda t: t["date"] or "", reverse=True)
    d90 = (TODAY - timedelta(days=90)).isoformat()
    r["insiders"] = {"trades": trades[:12],
                     "buy_90d": sum(t["value"] for t in trades if t["code"] == "P" and (t["date"] or "") >= d90),
                     "buyers_90d": len({t["who"] for t in trades if t["code"] == "P" and (t["date"] or "") >= d90}),
                     "sell_90d": sum(t["value"] for t in trades if t["code"] == "S" and not t["planned"] and (t["date"] or "") >= d90)}
    # cash runway
    r["runway"] = runway((uw_get(f"/api/stock/{tk}/financials", ttl=72) or {}).get("data"))
    # institutional holders (latest 13F quarter)
    inst = (uw_get(f"/api/institution/{tk}/ownership", {"limit": 300, "order": "value", "order_direction": "desc"}, ttl=48) or {}).get("data") or []
    if inst:
        latest_q = max(x.get("report_date") or "" for x in inst)
        cur = [x for x in inst if x.get("report_date") == latest_q]
        so = fnum(cur[0].get("shares_outstanding")) or r.get("shares_out") or 0
        def row(x):
            hu = x.get("historical_units") or []
            prev = hu[1] if len(hu) > 1 else None
            units = fnum(x.get("units")) or 0
            status = "new" if prev == 0 else ("added" if (fnum(x.get("units_changed")) or 0) > 0 else ("trimmed" if (fnum(x.get("units_changed")) or 0) < 0 else "held"))
            return {"name": (x.get("name") or "").title(), "units": units, "value": fnum(x.get("value")),
                    "pct_out": units / so if so else None, "chg": fnum(x.get("units_changed")), "status": status,
                    "specialist": is_specialist(x.get("name"))}
        rows = [row(x) for x in cur]
        r["holders"] = {"quarter": latest_q, "count": len(rows),
                        "pct_out": sum(x["units"] for x in rows) / so if so else None,
                        "top": rows[:10], "specialists": [x for x in rows if x["specialist"]][:10],
                        "new": sum(1 for x in rows if x["status"] == "new"),
                        "added": sum(1 for x in rows if x["status"] == "added"),
                        "trimmed": sum(1 for x in rows if x["status"] == "trimmed")}
    # options-implied move into the next exact-dated catalyst
    if r["has_options"] and next_exact:
        ex = (uw_get(f"/api/stock/{tk}/expiry-breakdown", ttl=fast(4)) or {}).get("data") or []
        exp = pick_expiry([e.get("expires") or e.get("expiry") for e in ex if (e.get("expires") or e.get("expiry"))], date.fromisoformat(next_exact))
        if exp:
            v = (uw_get(f"/api/stock/{tk}/volatility/context", {"expiry": exp}, ttl=fast(4)) or {}).get("data") or {}
            e = v.get("expiry") or {}
            if e.get("implied_move_perc") is not None:
                r["implied"] = {"expiry": e.get("expiry"), "move_pct": e.get("implied_move_perc"), "move_usd": e.get("implied_move"),
                                "iv": e.get("iv"), "iv_rank": v.get("iv_rank"), "for_date": next_exact}
    # SEC: 13D/13G stakes and dilution filings
    if cik:
        r["stakes"], r["dilution"] = sec_stakes(cik, ttl=fast(6))
        r["cik"] = cik
    return r


# --------------------------------------------------------------------------- main

def _auth():
    if "Authorization" not in _uw.headers:
        _uw.headers.update({"Authorization": f"Bearer {uw_token()}", "Accept": "application/json"})


def _sec_tickers():
    return (sec_get("https://www.sec.gov/files/company_tickers.json", as_json=True, ttl=24) or {}).values()


def cik_map():
    return {v["ticker"].upper(): v["cik_str"] for v in _sec_tickers()}


def sec_names():
    return {v["ticker"].upper(): v["title"] for v in _sec_tickers()}


def run(quick=False, days=150, min_mcap=25e6, all_cards=False, log=print):
    """Full refresh. Writes public/data.json (+ public/t/<TICKER>.json cards). Returns a short stats dict.
    all_cards=False pre-builds cards only for names with a catalyst (the app builds other cards live on click)."""
    global TODAY
    TODAY = date.today()
    _auth()
    t0 = time.time()

    log("1/5 Searching SEC filings")
    hits = fts_hits(days, 300 if quick else 2000)
    hits.sort(key=lambda h: h["date"] or "", reverse=True)
    if quick:
        hits = hits[:150]

    log(f"2/5 Reading {len(hits)} filings")
    sec_raw, done = [], 0
    with ThreadPoolExecutor(6) as pool:
        futs = {pool.submit(load_doc_events, h): h for h in hits}
        for f in as_completed(futs):
            h = futs[f]; done += 1
            for ev in f.result() or []:
                sec_raw.append((h, ev))
            if done % 250 == 0:
                log(f"2/5 Reading filings: {done}/{len(hits)}")

    log("3/5 FDA calendar feed")
    events = merge_events(sec_raw, uw_fda_events())

    log("4/5 Healthcare universe")
    uni = healthcare_universe(min_mcap)
    tmap = cik_map()
    names = dict(uni)
    for e in events:
        names.setdefault(e["ticker"], e.get("company"))
    events, n_drop = drop_third_party(events, names)
    log(f"4/5 Dropped {n_drop} events that quote another company's catalyst")
    tickers = sorted({e["ticker"] for e in events} | (set(uni) if all_cards else set()))

    log(f"5/5 Company data for {len(tickers)} names")
    data = {}
    def job(tk):
        exact = sorted(e["start"] for e in events if e["ticker"] == tk and e["precision"] == "day" and e["start"] >= TODAY.isoformat())
        cik = next((e.get("cik") for e in events if e["ticker"] == tk and e.get("cik")), None) or tmap.get(tk)
        try:
            return tk, enrich(tk, cik, exact[0] if exact else None)
        except Exception as ex:  # one bad ticker must not stop the build
            log(f"   ! {tk}: {ex}")
            return tk, None
    with ThreadPoolExecutor(8) as pool:
        for i, f in enumerate(as_completed([pool.submit(job, t) for t in tickers]), 1):
            tk, r = f.result()
            if r:
                data[tk] = r
            if i % 50 == 0:
                log(f"5/5 Company data: {i}/{len(tickers)}")
    events = [e for e in events if e["ticker"] in data]
    for e in events:
        e.pop("cik", None)
        e.setdefault("company", data[e["ticker"]]["name"])

    tdir = OUT.parent / "t"
    tdir.mkdir(parents=True, exist_ok=True)
    for old in tdir.glob("*.json"):
        if old.stem not in data:
            old.unlink()
    for tk, r in data.items():
        (tdir / f"{tk}.json").write_text(json.dumps(r, separators=(",", ":"), default=str), encoding="utf-8")
    universe = {tk: nm for tk, nm in uni.items()}
    for tk, r in data.items():
        universe.setdefault(tk, r.get("name"))
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "today": TODAY.isoformat(),
               "events": events, "tickers": {tk: summary(r) for tk, r in data.items()},
               "universe": {tk: (nm or "").title() for tk, nm in sorted(universe.items())},
               "notes": {"filings_window_days": days, "filings_read": len(hits), "universe_min_mcap": min_mcap}}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), default=str), encoding="utf-8")
    tmp.replace(OUT)   # atomic, so the app never reads a half-written file
    stats = {"events": len(events), "cards": len(data), "universe": len(universe), "seconds": round(time.time() - t0)}
    log(f"Done: {stats}")
    return stats


# --------------------------------------------------------------------------- live, one ticker

FILING_FORMS = {"8-K", "8-K/A", "10-Q", "10-K", "6-K", "20-F"}


def filings_since(cik: int, since: str, limit=6):
    """Company filings (8-K, 10-Q, ...) on or after `since`, newest first, straight from EDGAR."""
    s = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", as_json=True, ttl=0.05) or {}
    rec = (s.get("filings") or {}).get("recent") or {}
    out = []
    for i, form in enumerate(rec.get("form", [])):
        if form in FILING_FORMS and rec["filingDate"][i] >= since:
            out.append({"form": form, "date": rec["filingDate"][i], "adsh": rec["accessionNumber"][i], "primary": rec["primaryDocument"][i]})
    return out[:limit]


def live_events(tk: str, cik: int, company: str, since: str):
    """Catalyst events in filings made since the last full refresh (press-release exhibits included)."""
    raw = []
    for f in filings_since(cik, since):
        folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{f['adsh'].replace('-', '')}"
        idx = sec_get(folder + "/index.json", as_json=True, ttl=24 * 30) or {}
        names = [it["name"] for it in (idx.get("directory") or {}).get("item", [])]
        docs = [f["primary"]] + [n for n in names if re.search(r"ex-?99|dex99", n, re.I) and n.lower().endswith((".htm", ".html"))]
        for d in dict.fromkeys(docs):
            hit = {"id": f"{f['adsh']}:{d}", "ticker": tk, "cik": cik, "form": f["form"], "date": f["date"], "company": company}
            for ev in load_doc_events(hit) or []:
                raw.append((hit, ev))
    return merge_events(raw, [])


def live_card(tk: str, known_events: list, since: str | None):
    """Fresh card for any ticker: re-pulls price, insiders, filings and options, and checks new filings for catalysts.
    Returns (card or None, new_events)."""
    global TODAY
    TODAY = date.today()
    _auth()
    _tl.live = True
    tk = tk.upper()
    cik = cik_map().get(tk)
    new = []
    if cik and since:
        name = ((uw_get(f"/api/stock/{tk}/info", ttl=24) or {}).get("data") or {}).get("full_name") or tk
        for e in live_events(tk, cik, name.title(), since):
            if not any(k["type"] == e["type"] and not (k["end"] < e["start"] or e["end"] < k["start"]) for k in known_events):
                e["new"] = True
                e.pop("cik", None)
                new.append(e)
    allev = sorted(known_events + new, key=lambda e: e["end"])
    exact = [e["start"] for e in allev if e["precision"] == "day" and e["start"] >= TODAY.isoformat()]
    return enrich(tk, cik, exact[0] if exact else None, fresh=True), new


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="cap SEC documents for a fast test")
    ap.add_argument("--days", type=int, default=150, help="look-back window for filings")
    ap.add_argument("--min-mcap", type=float, default=25e6, help="smallest company kept in the searchable universe")
    ap.add_argument("--all-cards", action="store_true", help="also pre-build cards for every healthcare name")
    a = ap.parse_args()
    run(a.quick, a.days, a.min_mcap, a.all_cards)


GENERIC_FIRST = {"united", "american", "national", "global", "medical", "health", "healthcare", "pharma", "applied", "advanced",
                 "precision", "clinical", "international", "general", "first", "biotech", "bio", "the", "new", "life", "cell",
                 "gene", "therapeutics", "pharmaceuticals", "biosciences", "biopharma", "sciences", "group", "holdings", "capital",
                 "innovative", "integrated", "regional", "select", "universal", "pacific", "atlantic", "north", "south", "west", "east",
                 "community", "premier", "alpha", "beta", "omega", "nova", "vital", "standard", "focus", "summit", "harbor", "frontier"}
PARTNER = re.compile(r"\bpartner|collaborat|\blicens(?:ed|ee|ees|ing|or)\b|co-develop|agreement with|our (?:program|product|candidate)", re.I)


def core_name(full: str):
    """'CAPRICOR THERAPEUTICS, INC.' -> 'Capricor'; 'ZAI LAB LTD' -> 'Zai Lab'. None when too generic to match safely."""
    words = re.findall(r"[A-Za-z][A-Za-z0-9&'.-]*", full or "")
    if not words:
        return None
    w0 = words[0].strip(".").title()
    if len(w0) >= 5 and w0.lower() not in GENERIC_FIRST:
        return w0
    if len(words) > 1 and w0.lower() not in GENERIC_FIRST and len(w0) >= 2:
        two = f"{w0} {words[1].strip('.').title()}"
        if words[1].lower() not in ("inc", "corp", "co", "ltd", "plc", "sa", "ag", "nv", "llc"):
            return two
    return None


def drop_third_party(events, names: dict):
    """Companies often quote a competitor's PDUFA date in their own filings. If the quote names another
    tracked company and not the filer (and isn't about a partnership), the event isn't the filer's."""
    cores = {}
    for tk, nm in names.items():
        c = core_name(nm)
        if c:
            cores.setdefault(c, set()).add(tk)
    rx = re.compile(r"\b(" + "|".join(re.escape(c) for c in sorted(cores, key=len, reverse=True)) + r")\b") if cores else None
    kept, dropped = [], 0
    for e in events:
        q = e.get("quote") or ""
        own = {core_name(names.get(e["ticker"]) or ""), core_name(e.get("company") or "")} - {None}
        others = {m.group(1) for m in rx.finditer(q)} - own if rx else set()
        others = {o for o in others if e["ticker"] not in cores.get(o, set())}
        if others and not any(o in q for o in own) and not PARTNER.search(q):
            dropped += 1
            continue
        kept.append(e)
    return kept, dropped


def summary(r):
    """The few fields the calendar, screens and search need; everything else lives in t/<TICKER>.json."""
    d90 = (TODAY - timedelta(days=90)).isoformat()
    s, rw, ins = r.get("short") or {}, r.get("runway") or {}, r.get("insiders") or {}
    return {"name": r.get("name"), "mcap": r.get("mcap"), "price": r.get("price"), "chg_1m": r.get("chg_1m"),
            "short": {k: s.get(k) for k in ("si_float", "dtc", "fee")} if s else None,
            "runway": {k: rw.get(k) for k in ("months", "runs_out", "qtr_burn")} if rw else None,
            "implied": r.get("implied"),
            "insiders": {"buy_90d": ins.get("buy_90d", 0), "buyers_90d": ins.get("buyers_90d", 0)},
            "stakes": [x for x in r.get("stakes") or [] if x["date"] >= d90 and not x["form"].endswith("/A") and (x.get("pct") or 0) >= 5][:3],
            "dilution": [x for x in r.get("dilution") or [] if x["date"] >= d90 and x["form"].startswith("424B")][:3]}


def healthcare_universe(min_mcap: float):
    """Every Healthcare-sector common stock / ADR above min_mcap, from the UW screener."""
    rows, seen, page = {}, set(), 0
    while page < 20:
        q = {"sectors[]": "Healthcare", "order": "marketcap", "order_direction": "desc", "issue_types[]": ["Common Stock", "ADR"],
             "min_marketcap": str(int(min_mcap)), "limit": 500}
        if page:
            q["offset"] = page
        batch = (uw_get("/api/screener/stocks", q, ttl=12) or {}).get("data") or []
        fresh = [b for b in batch if b.get("ticker") and b["ticker"] not in seen]
        names = sec_names()
        for b in fresh:
            seen.add(b["ticker"]); rows[b["ticker"]] = b.get("full_name") or names.get(b["ticker"].upper())
        if len(batch) < 50 or not fresh:
            break
        page += 1
    return rows


if __name__ == "__main__":
    main()
