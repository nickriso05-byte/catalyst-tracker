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
UW_RL = RateLimiter(10)
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


HITS_FILE = CACHE / "docs" / "_hits.json"


def fts_hits(days: int, max_per_query: int):
    """Filings that mention catalyst phrases. The list from the last run is saved next to the sentence cache,
    so normally only the last few days are searched again."""
    window_start = (TODAY - timedelta(days=days)).isoformat()
    try:
        saved = {h["id"]: h for h in json.loads(HITS_FILE.read_text(encoding="utf-8")) if (h.get("date") or "") >= window_start}
    except Exception:
        saved = {}
    newest = max((h["date"] for h in saved.values() if h.get("date")), default=None)
    if newest and newest >= (TODAY - timedelta(days=30)).isoformat():
        start = (date.fromisoformat(newest) - timedelta(days=3)).isoformat()
    else:
        start, saved = window_start, {}
    hits = dict(saved)
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
    HITS_FILE.write_text(json.dumps(sorted(hits.values(), key=lambda h: h["id"]), separators=(",", ":")), encoding="utf-8")
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
    ACCT = re.compile(r"\blease|\brent\b|liabilit|ROU asset|remeasur|contractual|amortiz|accrued|interest expense", re.I)
    def quality(e):   # prefer the sentence that names the drug, and not accounting boilerplate
        q = (e.get("quote") or "").lower()
        return (2 if e.get("drug") and e["drug"].lower() in q else 0) - (3 if ACCT.search(q) else 0)
    out.sort(key=lambda e: (PREC_RANK[e["precision"]], -quality(e), 0 if e.get("drug") else 1))
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


SPECIALISTS += ["bvf", "logos", "braidwell", "casdin", "bain capital life"]

# Top-tier biotech specialists: the 26-fund tier-1 roster used by the colleague's Verdad-style screener,
# written as fragments of their 13F filer names. The score's specialist factor counts only these.
TIER1 = ["orbimed", "baker bros", "ra capital", "sphera", "perceptive", "rock springs", "soleus", "rtw investments",
         "logos global", "deep track", "braidwell", "tcg crossover", "vivo capital", "deerfield", "cormorant", "bvf",
         "frazier", "redmile", "casdin", "ecor1", "commodore", "paradigm biocapital", "avoro", "samsara",
         "bain capital life", "fairmount"]


# Whole-word matching: a bare substring test let "ra capital" match "Yakira Capital", "Mora Capital", etc.
_SPEC_RX = re.compile(r"\b(?:" + "|".join(re.escape(x.strip()) for x in SPECIALISTS) + r")\b")
_TIER1_RX = re.compile(r"\b(?:" + "|".join(re.escape(x.strip()) for x in TIER1) + r")\b")


def is_specialist(name: str) -> bool:
    return bool(_SPEC_RX.search((name or "").lower()))


def is_tier1(name: str) -> bool:
    return bool(_TIER1_RX.search((name or "").lower()))


# --------------------------------------------------------------------------- Verdad-style score (our own)
# Factors from Verdad's "Biotech Investing" paper that we can measure. Peer momentum is left out:
# Verdad defines peers by clinical-trial similarity, which we don't have.
#   spec  share of shares held by top-tier specialist funds (higher is better)
#   short short interest as % of float (lower is better)
#   ins   open-market insider buying in 90 days, in basis points of market cap (higher is better)
#   value annual spending (R&D + SG&A) per dollar of enterprise value ex-cash (higher = cheaper = better)
FACTORS = ("spec", "short", "ins", "value")


def spend_and_ev(fin, mcap):
    """Annualized R&D + SG&A from the last 4 quarters, and enterprise value ex-cash (market cap + debt - cash)."""
    inc = sorted([i for i in (fin or {}).get("income_statements", []) if i.get("report_type") == "quarterly"],
                 key=lambda i: i["fiscal_date_ending"], reverse=True)[:4]
    bs = sorted([b for b in (fin or {}).get("balance_sheets", []) if b.get("report_type") == "quarterly"],
                key=lambda b: b["fiscal_date_ending"], reverse=True)
    spend = [(fnum(i.get("research_and_development")) or 0) + (fnum(i.get("selling_general_and_administrative")) or 0) for i in inc]
    spend = [s for s in spend if s > 0]
    if len(spend) < 2 or not bs or not mcap:
        return None
    b = bs[0]
    cash = max(fnum(b.get("cash_and_short_term_investments")) or 0,
               (fnum(b.get("cash_and_cash_equivalents")) or 0) + (fnum(b.get("short_term_investments")) or 0))
    debt = fnum(b.get("short_long_term_debt_total")) or 0
    return {"spend": sum(spend) / len(spend) * 4, "ev": mcap + debt - cash}


def _quarter_ends(n=2):
    """The last n calendar quarter-ends before today, newest first."""
    out, y, q = [], TODAY.year, (TODAY.month - 1) // 3   # q = completed quarters this year
    while len(out) < n:
        if q == 0:
            y, q = y - 1, 4
        out.append(date(y, 3 * q, 31 if q in (1, 4) else 30).isoformat())
        q -= 1
    return out


def inst_rows(tk: str, ttl=12):
    """13F holders of a ticker from the latest quarter AND the one before. UW's default only returns funds that
    have already filed the newest quarter, so mid-season it would drop every fund that hasn't filed yet."""
    p = {"limit": 500, "order": "value", "order_direction": "desc"}
    rows = list((uw_get(f"/api/institution/{tk}/ownership", p, ttl=ttl) or {}).get("data") or [])
    for qe in _quarter_ends(2):
        rows += (uw_get(f"/api/institution/{tk}/ownership", {**p, "date": qe}, ttl=24 * 7) or {}).get("data") or []
    return rows


def latest_holdings(inst):
    """Each filer's most recent 13F position. During a filing season some funds have filed the new quarter and
    others haven't yet; taking only the newest report date would drop everyone who hasn't filed."""
    cutoff = (TODAY - timedelta(days=200)).isoformat()
    best = {}
    for x in inst:
        rd = x.get("report_date") or ""
        if rd < cutoff:
            continue
        k = x.get("cik") or (x.get("name") or "").lower()
        if k not in best or rd > (best[k].get("report_date") or ""):
            best[k] = x
    rows = [x for x in best.values() if (fnum(x.get("units")) or 0) > 0]
    rows.sort(key=lambda x: fnum(x.get("value")) or 0, reverse=True)
    return rows, max((x.get("report_date") or "" for x in rows), default=None)


from verdad_fund_ciks import TIER1_FUNDS, TIER2_FUNDS   # the colleague's screener roster, matched by SEC filer ID

_T1_CIK = {int(c): n for n, c in TIER1_FUNDS.items()}
_T2_CIK = {int(c): n for n, c in TIER2_FUNDS.items()}
W_TIER1, W_TIER2 = 100, 40   # his screener's points per fund


def _cik(x):
    try:
        return int(x.get("cik") or 0)
    except (TypeError, ValueError):
        return 0


def tier1_share(inst_rows, shares_out):
    """Roster funds holding the stock, matched by 13F filer CIK (names fall back only when a row has no CIK)."""
    t1, t2 = {}, {}
    for x in inst_rows:
        c = _cik(x)
        if c in _T1_CIK:
            t1[_T1_CIK[c]] = t1.get(_T1_CIK[c], 0) + (fnum(x.get("units")) or 0)
        elif c in _T2_CIK:
            t2[_T2_CIK[c]] = t2.get(_T2_CIK[c], 0) + (fnum(x.get("units")) or 0)
        elif not c and is_tier1(x.get("name")):
            nm = (x.get("name") or "").title()
            t1[nm] = t1.get(nm, 0) + (fnum(x.get("units")) or 0)
    units = sum(t1.values()) + sum(t2.values())
    return {"pct": units / shares_out if shares_out else None, "n": len(t1), "n2": len(t2),
            "funds": sorted(t1, key=lambda k: -t1[k]), "funds2": sorted(t2, key=lambda k: -t2[k])}


def factor_values(mcap, si_float, buy_90d, se, spec):
    """Raw factor values, oriented so higher is better. None when the input is missing."""
    f = {}
    if spec and spec.get("n") is not None:
        # Verdad: the strongest signal is several specialists owning the same stock. Weight funds the way his
        # screener does (100 per tier-1, 40 per tier-2); the share of stock they hold only breaks ties.
        f["spec"] = W_TIER1 * spec["n"] + W_TIER2 * (spec.get("n2") or 0) + min(spec.get("pct") or 0, 0.5)
    if si_float is not None:
        f["short"] = -si_float
    if mcap:
        f["ins"] = (buy_90d or 0) / mcap * 1e4
    if se:
        f["value"] = 1e6 if se["ev"] <= 0 else se["spend"] / se["ev"]   # trading below cash: cheapest
    return f


def _pctile(sorted_vals, v):
    """Mid-rank percentile (0-100) of v among sorted_vals, so ties (e.g. no insider buys) land in the middle."""
    import bisect
    if not sorted_vals:
        return None
    lo, hi = bisect.bisect_left(sorted_vals, v), bisect.bisect_right(sorted_vals, v)
    return 100 * (lo + hi) / 2 / len(sorted_vals)


def score_from(factors: dict, dist: dict):
    """Equal-weight average of factor percentiles vs. the refresh's distribution. Needs at least 3 factors."""
    parts = {k: _pctile(dist.get(k) or [], factors[k]) for k in FACTORS if k in factors and dist.get(k)}
    parts = {k: v for k, v in parts.items() if v is not None}
    if len(parts) < 3:
        return None
    score = round(sum(parts.values()) / len(parts))
    allsc = dist.get("_scores") or []
    rank = 1 + sum(1 for s in allsc if s > score) if allsc else None
    return {"score": score, "rank": rank, "n": len(allsc) or None, "parts": {k: round(v) for k, v in parts.items()}}


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
    # SEC's ticker list has the current company name; UW's can lag a rename (CBIO: Catalyst Biosciences -> Crescent Biopharma)
    r = {"name": (sec_names().get(tk.upper()) or info.get("full_name") or "").title(), "sector": info.get("sector"), "mcap": fnum(info.get("marketcap")),
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
    # cash runway + spending vs. enterprise value
    fin = (uw_get(f"/api/stock/{tk}/financials", ttl=72) or {}).get("data")
    r["runway"] = runway(fin)
    r["value"] = spend_and_ev(fin, r.get("mcap"))
    # institutional holders (latest 13F quarter)
    inst = inst_rows(tk)
    cur, latest_q = latest_holdings(inst)
    if cur:
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
        r["tier1"] = tier1_share(cur, so)
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
    r["factors"] = factor_values(r.get("mcap"), (r.get("short") or {}).get("si_float"), r["insiders"]["buy_90d"], r.get("value"), r.get("tier1"))
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


# Historical averages by stage (BIO / Informa Pharma Intelligence / QLS, "Clinical Development Success Rates
# 2011-2020", all indications). A placeholder until the drug-specific odds model is validated.
BASE_SOURCE = "BIO/Informa, Clinical Development Success Rates 2011-2020"
_PH3 = re.compile(r"phase\s*(?:3|iii)\b|pivotal|registrational", re.I)
_PH2 = re.compile(r"phase\s*(?:2|ii)(?:[ab])?\b", re.I)
_PH1 = re.compile(r"phase\s*(?:1|i)(?:[ab])?\b", re.I)


def baseline_odds(e):
    t, q = e.get("type"), e.get("quote") or ""
    if t in ("PDUFA date", "Advisory committee"):
        return {"p": 0.906, "what": "of drugs that reach an FDA filing are eventually approved (not always on the first review)"}
    if t == "Data readout":
        if _PH3.search(q):
            return {"p": 0.578, "what": "of Phase 3 drugs go on to an FDA filing"}
        if _PH2.search(q):
            return {"p": 0.289, "what": "of Phase 2 drugs make it to Phase 3"}
        if _PH1.search(q):
            return {"p": 0.520, "what": "of Phase 1 drugs make it to Phase 2"}
    return None


def run(quick=False, days=150, min_mcap=10e6, log=print, **_):
    """Full refresh, written in two passes so the calendar shows fast on a cold server:
      pass 1 (seconds): catalysts + market cap, short interest and insider buys from bulk calls -> data.json
      pass 2 (about a minute): cash runway, implied moves, 13D/13G and share-sale filings per company -> data.json
    Company cards themselves are built live in the app when opened."""
    global TODAY
    TODAY = date.today()
    _auth()
    t0 = time.time()

    log("Searching SEC filings")
    hits = fts_hits(days, 300 if quick else 2000)
    hits.sort(key=lambda h: h["date"] or "", reverse=True)
    if quick:
        hits = hits[:150]
    cached = sum(1 for h in hits if (CACHE / "docs" / (re.sub(r"[^A-Za-z0-9_.-]", "_", h["id"]) + ".json")).exists())
    log(f"Reading {len(hits)} filings ({len(hits) - cached} new)")
    sec_raw, done = [], 0
    with ThreadPoolExecutor(8) as pool:
        futs = {pool.submit(load_doc_events, h): h for h in hits}
        for f in as_completed(futs):
            h = futs[f]; done += 1
            for ev in f.result() or []:
                sec_raw.append((h, ev))
            if done % 250 == 0:
                log(f"Reading filings: {done}/{len(hits)}")

    log("FDA calendar feed and healthcare universe")
    events = merge_events(sec_raw, uw_fda_events())
    uni = healthcare_rows(min_mcap)
    names = {tk: r["name"] for tk, r in uni.items()}
    for e in events:
        names.setdefault(e["ticker"], e.get("company"))
    events, n_drop = drop_third_party(events, names)
    # The UW screener misses some healthcare names (e.g. recent IPOs), so check the rest one by one (cached a day).
    missing = sorted({e["ticker"] for e in events} - set(uni))
    with ThreadPoolExecutor(10) as pool:
        for tk, info in zip(missing, pool.map(lambda t: (uw_get(f"/api/stock/{t}/info", ttl=24) or {}).get("data") or {}, missing)):
            if (info.get("sector") or "") == "Healthcare" and (fnum(info.get("marketcap")) or 0) >= min_mcap:
                uni[tk] = {"name": info.get("full_name") or names.get(tk), "mcap": fnum(info.get("marketcap"))}
    events = [e for e in events if e["ticker"] in uni]          # healthcare, above the size floor
    tickers = sorted({e["ticker"] for e in events})

    # ---- pass 1: bulk numbers
    log(f"Bulk data for {len(tickers)} companies")
    summ = {tk: {"name": (uni[tk]["name"] or tk).title(), "mcap": uni[tk]["mcap"], "short": None, "runway": None, "implied": None,
                 "insiders": {"buy_90d": 0, "buyers_90d": 0}, "stakes": [], "dilution": []} for tk in tickers}
    for tk, s in bulk_short(tickers).items():
        summ[tk]["short"] = s
    for tk, b in bulk_insider_buys(tickers).items():
        summ[tk]["insiders"] = b
    for e in events:
        e.pop("cik", None)
        e["company"] = summ[e["ticker"]]["name"]
        b = baseline_odds(e)
        if b:
            e["base"] = {**b, "source": BASE_SOURCE}
    universe = {tk: (r["name"] or "").title() for tk, r in sorted(uni.items())}
    score_dist = {}

    def write(phase):
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"), "today": TODAY.isoformat(),
                   "phase": phase, "events": events, "tickers": summ, "universe": universe, "score_dist": score_dist,
                   "notes": {"filings_window_days": days, "filings_read": len(hits), "competitor_quotes_dropped": n_drop,
                             "universe_min_mcap": min_mcap}}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":"), default=str), encoding="utf-8")
        tmp.replace(OUT)   # atomic, so the app never reads a half-written file
    write("calendar")
    log(f"Calendar ready ({len(events)} events, {time.time() - t0:.0f}s). Adding runway, implied moves, 13D/13G")

    # ---- pass 2: per-company detail
    tmap = cik_map()

    def job(tk):
        exact = sorted(e["start"] for e in events if e["ticker"] == tk and e["precision"] == "day" and e["start"] >= TODAY.isoformat())
        out = {"runway": None, "implied": None, "stakes": [], "dilution": [], "value": None, "tier1": None}
        try:
            fin = (uw_get(f"/api/stock/{tk}/financials", ttl=72) or {}).get("data")
            rw = runway(fin)
            out["runway"] = {k: rw.get(k) for k in ("months", "runs_out", "qtr_burn")} if rw else None
            out["value"] = spend_and_ev(fin, summ[tk]["mcap"])
            cur, _q = latest_holdings(inst_rows(tk))
            out["tier1"] = tier1_share(cur, fnum(cur[0].get("shares_outstanding")) if cur else None) if cur else {"pct": 0.0, "n": 0, "n2": 0, "funds": [], "funds2": []}
            if exact:
                out["implied"] = implied_move(tk, exact[0])
            cik = tmap.get(tk)
            if cik:
                out["stakes"], out["dilution"] = sec_recent(cik)
        except Exception as ex:  # one bad ticker must not stop the build
            log(f"   ! {tk}: {ex}")
        return tk, out

    with ThreadPoolExecutor(12) as pool:
        for i, f in enumerate(as_completed([pool.submit(job, t) for t in tickers]), 1):
            tk, out = f.result()
            summ[tk].update(out)
            if i % 100 == 0:
                log(f"Company detail: {i}/{len(tickers)}")
    # Our Verdad-style score: each factor ranked against every company in this refresh, then averaged.
    for tk in tickers:
        t = summ[tk]
        t["factors"] = factor_values(t["mcap"], (t.get("short") or {}).get("si_float"), (t.get("insiders") or {}).get("buy_90d"), t.get("value"), t.get("tier1"))
    dist = {k: sorted(summ[tk]["factors"][k] for tk in tickers if k in summ[tk]["factors"]) for k in FACTORS}
    prelim = [score_from(summ[tk]["factors"], dist) for tk in tickers]
    dist["_scores"] = sorted(p["score"] for p in prelim if p)
    for tk in tickers:
        summ[tk]["vscore"] = score_from(summ[tk]["factors"], dist)
    score_dist.clear(); score_dist.update(dist)
    write("complete")
    log(f"Scores ready ({time.time() - t0:.0f}s). Saving full company cards")

    # Pass 3: a full card for every company on the calendar, so the app can open one instantly
    # (the app then refreshes it live in the background). Mostly cache hits from pass 2.
    tdir = OUT.parent / "t"
    tdir.mkdir(parents=True, exist_ok=True)

    def card_job(tk):
        exact = sorted(e["start"] for e in events if e["ticker"] == tk and e["precision"] == "day" and e["start"] >= TODAY.isoformat())
        try:
            c = enrich(tk, tmap.get(tk), exact[0] if exact else None)
        except Exception as ex:
            log(f"   ! card {tk}: {ex}")
            return tk, None
        if c:
            c["vscore"] = score_from(c.get("factors") or {}, dist)
            c["saved_at"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        return tk, c

    saved = 0
    with ThreadPoolExecutor(8) as pool:
        for i, f in enumerate(as_completed([pool.submit(card_job, t) for t in tickers]), 1):
            tk, c = f.result()
            if c:
                tmp = tdir / f"{tk}.tmp"
                tmp.write_text(json.dumps(c, separators=(",", ":"), default=str), encoding="utf-8")
                tmp.replace(tdir / f"{tk}.json")
                saved += 1
            if i % 100 == 0:
                log(f"Company cards: {i}/{len(tickers)}")
    stats = {"events": len(events), "companies": len(tickers), "cards": saved, "universe": len(universe), "seconds": round(time.time() - t0)}
    log(f"Done: {stats}")
    return stats


def _chunks(xs, n):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


def bulk_short(tickers):
    """Latest FINRA short interest for many tickers in a few calls."""
    out = {}
    since = (TODAY - timedelta(days=45)).isoformat()
    for ch in _chunks(tickers, 80):
        off = 0
        while True:
            rows = (uw_get("/api/short_screener", {"tickers": ",".join(ch), "min_market_date": since, "limit": 500, "offset": off}, ttl=12) or {}).get("data") or []
            for r in rows:
                tk, md = r.get("symbol"), r.get("market_date") or ""
                if tk in out and out[tk]["as_of"] >= md:
                    continue
                out[tk] = {"as_of": md, "si_float": fnum(r.get("si_float")), "dtc": fnum(r.get("days_to_cover")), "fee": fnum(r.get("fee_rate"))}
            if len(rows) < 500:
                break
            off += 500
    return out


def bulk_insider_buys(tickers):
    """Open-market insider buys (Form 4, code P) in the last 90 days, for many tickers in a few calls."""
    agg = {}
    start = (TODAY - timedelta(days=90)).isoformat()
    for ch in _chunks(tickers, 60):
        cursor = None
        for _ in range(20):
            p = {"ticker_symbol": ",".join(ch), "transaction_codes[]": "P", "form_types[]": "4", "start_date": start, "limit": 500}
            if cursor:
                p["cursor"] = cursor
            j = uw_get("/api/insider/transactions", p, ttl=6) or {}
            for t in j.get("data") or []:
                if "common" not in (t.get("security_title") or "common").lower():
                    continue
                a = agg.setdefault(t["ticker"], {"buy_90d": 0.0, "who": set()})
                a["buy_90d"] += abs(fnum(t.get("amount")) or 0) * (fnum(t.get("price")) or 0)
                a["who"].add(t.get("owner_name"))
            cursor = (j.get("cursor") or {}).get("next")
            if not cursor:
                break
    return {tk: {"buy_90d": a["buy_90d"], "buyers_90d": len(a["who"])} for tk, a in agg.items()}


def implied_move(tk, cat_date: str):
    ex = (uw_get(f"/api/stock/{tk}/expiry-breakdown", ttl=4) or {}).get("data") or []
    exp = pick_expiry([e.get("expires") for e in ex if e.get("expires")], date.fromisoformat(cat_date))
    if not exp:
        return None
    v = (uw_get(f"/api/stock/{tk}/volatility/context", {"expiry": exp}, ttl=4) or {}).get("data") or {}
    e = v.get("expiry") or {}
    if e.get("implied_move_perc") is None:
        return None
    return {"expiry": e.get("expiry"), "move_pct": e.get("implied_move_perc"), "move_usd": e.get("implied_move"),
            "iv": e.get("iv"), "iv_rank": v.get("iv_rank"), "for_date": cat_date}


def sec_recent(cik: int):
    """New 13D/13G (5%+, not amendments) and share-sale prospectuses in the last 90 days. Parses only those XMLs."""
    s = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", as_json=True, ttl=6)
    if not s:
        return [], []
    rec = s["filings"]["recent"]
    d90 = (TODAY - timedelta(days=90)).isoformat()
    stakes, dil = [], []
    for i, form in enumerate(rec["form"]):
        fd = rec["filingDate"][i]
        if fd < d90:
            break
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{rec['accessionNumber'][i].replace('-', '')}"
        if form in ("SCHEDULE 13D", "SCHEDULE 13G") and len(stakes) < 3:
            x = sec_get(f"{base}/primary_doc.xml", ttl=24 * 365)
            info = parse_13x(x) if x else {}
            if (info.get("pct") or 0) >= 5:
                stakes.append({"form": form.replace("SCHEDULE ", ""), "date": fd, "url": f"{base}/{rec['primaryDocument'][i]}", **info})
        elif form.startswith("424B") and len(dil) < 3:
            dil.append({"form": form, "date": fd, "url": f"{base}/{rec['primaryDocument'][i]}"})
    return stakes, dil


def healthcare_rows(min_mcap: float):
    """Every Healthcare-sector common stock / ADR above min_mcap: {ticker: {name, mcap}} from the UW screener."""
    rows, seen, page = {}, set(), 0
    names = sec_names()
    while page < 20:
        q = {"sectors[]": "Healthcare", "order": "marketcap", "order_direction": "desc", "issue_types[]": ["Common Stock", "ADR"],
             "min_marketcap": str(int(min_mcap)), "limit": 500}
        if page:
            q["offset"] = page
        batch = (uw_get("/api/screener/stocks", q, ttl=12) or {}).get("data") or []
        fresh = [b for b in batch if b.get("ticker") and b["ticker"] not in seen]
        for b in fresh:
            seen.add(b["ticker"])
            rows[b["ticker"]] = {"name": b.get("full_name") or names.get(b["ticker"].upper()), "mcap": fnum(b.get("marketcap"))}
        if len(batch) < 50 or not fresh:
            break
        page += 1
    return rows


def healthcare_universe(min_mcap: float):
    return {tk: r["name"] for tk, r in healthcare_rows(min_mcap).items()}


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
    ap.add_argument("--min-mcap", type=float, default=10e6, help="smallest company kept (market cap, $)")
    a = ap.parse_args()
    run(a.quick, a.days, a.min_mcap)


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
        # A drug code carries its owner's name: DYNE-251 is Dyne's, even when the sentence doesn't name Dyne.
        m = re.match(r"([A-Za-z]{3,6})-\d", e.get("drug") or "")
        if m:
            pre = m.group(1).lower()
            own_first = {(names.get(e["ticker"]) or e.get("company") or "").split(" ")[0].lower().strip(",.")}
            owner = [tk for tk, nm in names.items() if tk != e["ticker"] and (nm or "").split(" ")[0].lower().strip(",.") == pre]
            if owner and pre not in own_first and not PARTNER.search(q):
                others.add(owner[0])
        if others and not any(o in q for o in own) and not PARTNER.search(q):
            dropped += 1
            continue
        kept.append(e)
    return kept, dropped


if __name__ == "__main__":
    main()
