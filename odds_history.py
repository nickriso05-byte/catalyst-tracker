"""
odds_history.py: history of FDA decision dates (PDUFA) and their outcomes, from SEC filings.

For every 8-K / 6-K since 2005 that mentions a PDUFA date:
  1. pull the PDUFA date(s) stated in it (dates after the filing date),
  2. record the regulatory signals mentioned in the same filing (priority review, breakthrough, AdCom, ...).
Then for each company + PDUFA date, read the company's 8-K / 6-K filings around that date and label the outcome:
  approved, complete response letter (CRL), or unknown (extended, withdrawn, or not reported).

Output: odds/history.csv, one row per (company, PDUFA date). Re-running is cheap; SEC documents are cached.
Run:  python odds_history.py [--start 2005] [--end 2026]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import build

HERE = Path(__file__).parent
OUTDIR = HERE / "odds"
OUTDIR.mkdir(exist_ok=True)
HCACHE = build.CACHE / "hist"
HCACHE.mkdir(parents=True, exist_ok=True)

PDUFA_RX = re.compile(r"\bPDUFA\b|target action date|action date|goal date", re.I)
KEEP_RX = re.compile(r"PDUFA|action date|goal date|approv|complete response|priority review|breakthrough|fast track|orphan|"
                     r"advisory committee|resubmi|accelerated|supplemental|\bsNDA\b|\bsBLA\b|\bBLA\b|\bNDA\b|505\(b\)\(2\)|"
                     r"extend|extension|major amendment|label|inspection|manufactur|withdr", re.I)

# Signals known before the decision (all read from the company's own filings)
FEATURES = {
    "priority_review": r"\bpriority review\b",
    "breakthrough": r"\bbreakthrough therapy\b",
    "fast_track": r"\bfast track\b",
    "orphan": r"\borphan drug\b",
    "accelerated": r"\baccelerated approval\b",
    "rtor_or_voucher": r"real-time oncology review|priority review voucher|national priority voucher|commissioner'?s national priority",
    "adcom_planned": r"advisory committee (?:meeting )?(?:is |has been |was )?(?:planned|scheduled|will be held|to be held|meeting (?:on|in))",
    "adcom_none": r"(?:not|no)(?: currently)? (?:planning|plan|intend)(?:s|ing)? to hold an advisory committee|no advisory committee",
    "adcom_positive": r"advisory committee[^.]{0,120}\bvoted?\b[^.]{0,80}\b(?:in favor|favorabl|support)",
    "adcom_negative": r"advisory committee[^.]{0,120}\bvoted?\b[^.]{0,80}\bagainst\b",
    "resubmission": r"\bresubmi(?:ssion|tted)\b|class (?:1|2|i|ii) resubmission",
    "prior_crl": r"complete response letter",
    "supplemental": r"\bsNDA\b|\bsBLA\b|supplemental (?:new drug|biologics?) application|label expansion",
    "bla": r"\bBLA\b|biologics license application",
    "nda_505b2": r"505\(b\)\(2\)",
    "extended": r"major amendment|extend(?:ed|s)? the PDUFA|PDUFA[^.]{0,60}extended|extension of the PDUFA",
    "manufacturing": r"inspection|form 483|manufacturing facilit|third-party manufactur|\bCMC\b",
    "label_talks": r"label(?:ing)? (?:discussions|negotiations)|late-cycle (?:meeting|review)",
    "partnered": r"\bpartner\b|collaborat|licens(?:ee|ed|or)\b",
}
FEAT_RX = {k: re.compile(v, re.I) for k, v in FEATURES.items()}

APPROVED_RX = re.compile(
    r"(?:FDA|Food and Drug Administration)(?:\s*\(\s*FDA\s*\))?[^.]{0,60}?\b(?:has |had )?(?:approved|granted (?:full |accelerated |traditional |marketing )?approval)\b"
    r"|\b(?:announced|received|receives|receipt of)\b[^.]{0,40}\b(?:FDA |U\.S\. )?approval of\b"
    r"|\bapproved by the (?:U\.S\. )?(?:FDA|Food and Drug Administration)\b", re.I)
NOT_APPROVAL_RX = re.compile(r"\bif approved\b|potential(?:ly)? approv|seeking approval|to approve|for approval\b|expected approv|"
                             r"anticipat|accepted|acceptance|filing|priority review|designation|previously approved|"
                             r"approved (?:drug|product|therap|indication)s?\b|already approved|other approved|approved in (?:europe|japan|china|the eu)|"
                             r"European Commission|\bEMA\b|\bCHMP\b|Health Canada|\bPMDA\b|\bNMPA\b|\bMHRA\b|marketing authori[sz]ation", re.I)
CRL_RX = re.compile(r"\b(?:received|receives|issued|issues)\b[^.]{0,80}complete response letter|complete response letter[^.]{0,80}\b(?:received|issued)\b", re.I)
EXT_RX = FEAT_RX["extended"]


def split_sentences(text):
    return [s for s in build.SPLIT.split(text) if 30 <= len(s) <= 1500]


def cached_sentences(url):
    """Relevant sentences of one SEC document, cached on disk."""
    key = re.sub(r"[^A-Za-z0-9_.-]", "_", url.split("/Archives/edgar/data/")[-1])[:180] + ".json"
    p = HCACHE / key
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    raw = build.sec_get(url)
    if raw is None:
        return None
    sents = [s for s in split_sentences(build.html_to_text(raw)) if KEEP_RX.search(s)]
    p.write_text(json.dumps(sents), encoding="utf-8")
    return sents


# ------------------------------------------------------------------ 1. PDUFA mentions

def search_year(year):
    """All 8-K / 6-K documents in a year that mention a PDUFA date (searched quarter by quarter)."""
    hits = {}
    for q_start, q_end in ((f"{year}-01-01", f"{year}-03-31"), (f"{year}-04-01", f"{year}-06-30"),
                           (f"{year}-07-01", f"{year}-09-30"), (f"{year}-10-01", f"{year}-12-31")):
        off = 0
        while True:
            j = build.sec_get("https://efts.sec.gov/LATEST/search-index", {"q": '"PDUFA"', "forms": "8-K,6-K", "dateRange": "custom",
                              "startdt": q_start, "enddt": q_end, "from": off}, as_json=True, ttl=24 * 30 if year < date.today().year else 12)
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
                cik = int((s.get("ciks") or ["0"])[0])
                hits[h["_id"]] = {"id": h["_id"], "cik": cik, "ticker": tk, "date": s.get("file_date"),
                                  "company": re.sub(r"\s*\(.*$", "", names[0]).strip() if names else ""}
            off += 100
            if off >= j.get("hits", {}).get("total", {}).get("value", 0) or not page:
                break
    return list(hits.values())


def pdufa_mentions(hit):
    """(pdufa_date, sentence, features) found in one filing; only dates after the filing date count."""
    adsh, fn = hit["id"].split(":", 1)
    url = f"https://www.sec.gov/Archives/edgar/data/{hit['cik']}/{adsh.replace('-', '')}/{fn}"
    sents = cached_sentences(url)
    if not sents:
        return []
    filed = date.fromisoformat(hit["date"])
    feats = {k: int(any(rx.search(s) for s in sents)) for k, rx in FEAT_RX.items()}
    out = []
    for s in sents:
        km = PDUFA_RX.search(s)
        if not km:
            continue
        ds = [d for d in build.find_dates(s) if d["precision"] == "day" and filed < d["start"] <= filed + timedelta(days=420)]
        if not ds:
            continue
        d = min(ds, key=lambda d: abs(d["pos"] - km.start()) if d["pos"] >= km.start() else abs(d["pos"] - km.start()) + 40)
        out.append({"pdufa": d["start"].isoformat(), "sentence": s[:500], "drug": build.guess_drug(s, km.start()), "feats": feats})
    return out


# ------------------------------------------------------------------ 2. outcomes

def company_filings(cik):
    """All 8-K / 6-K filings of a company (recent + older pages of the SEC submissions file)."""
    s = build.sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json", as_json=True, ttl=24 * 7)
    if not s:
        return []
    blocks = [s["filings"]["recent"]]
    for f in s["filings"].get("files", []):
        b = build.sec_get(f"https://data.sec.gov/submissions/{f['name']}", as_json=True, ttl=24 * 30)
        if b:
            blocks.append(b)
    out = []
    for b in blocks:
        for i, form in enumerate(b.get("form", [])):
            if form in ("8-K", "6-K", "8-K/A"):
                out.append({"date": b["filingDate"][i], "adsh": b["accessionNumber"][i], "primary": b["primaryDocument"][i]})
    return out


def filing_docs(cik, f):
    """Primary document + press-release exhibits of one filing."""
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{f['adsh'].replace('-', '')}"
    idx = build.sec_get(folder + "/index.json", as_json=True, ttl=24 * 365) or {}
    names = [it["name"] for it in (idx.get("directory") or {}).get("item", [])]
    docs = [f["primary"]] + [n for n in names if re.search(r"ex-?99|dex99|ex99", n, re.I) and n.lower().endswith((".htm", ".html", ".txt"))]
    return [f"{folder}/{d}" for d in dict.fromkeys(docs) if d]


def classify(sents, drug=None):
    """approved / crl / extended / None for one filing's sentences."""
    crl = any(CRL_RX.search(s) for s in sents)
    appr = [s for s in sents if APPROVED_RX.search(s) and not NOT_APPROVAL_RX.search(s)]
    if drug:   # when we know the drug, the approval has to name it (multi-product companies approve other drugs too)
        appr = [s for s in appr if drug.lower() in s.lower()]
        crl = crl and any(drug.lower() in s.lower() for s in sents if "complete response" in s.lower()) or (crl and not any(
            "complete response" in s.lower() and drug.lower() not in s.lower() for s in sents))
    if crl and not appr:
        return "crl"
    if appr and not crl:
        return "approved"
    if crl and appr:
        return None    # both in one release (e.g. an approval that recaps an older CRL for another drug): too ambiguous to label
    if any(EXT_RX.search(s) for s in sents):
        return "extended"
    return None


def outcome_for(ev, filings):
    """Look at the company's 8-K / 6-K filings from 75 days before to 21 days after the PDUFA date."""
    pd = date.fromisoformat(ev["pdufa"])
    # Known drug: the filing must name it, so a wider window is safe. Unknown drug: stay close to the date.
    lo, hi = ((pd - timedelta(days=75)).isoformat(), (pd + timedelta(days=21)).isoformat()) if ev.get("drug") else \
             ((pd - timedelta(days=14)).isoformat(), (pd + timedelta(days=10)).isoformat())
    after_last_mention = ev["last_seen"]
    window = sorted([f for f in filings if lo <= f["date"] <= hi and f["date"] >= after_last_mention], key=lambda f: f["date"])
    for f in window:
        sents = []
        for url in filing_docs(ev["cik"], f):
            sents += cached_sentences(url) or []
        res = classify(sents, ev.get("drug"))
        if res == "extended":
            return "extended", f["date"]
        if res in ("approved", "crl"):
            return res, f["date"]
    return None, None


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2005)
    ap.add_argument("--end", type=int, default=date.today().year)
    a = ap.parse_args()

    hits = []
    for y in range(a.start, a.end + 1):
        hy = search_year(y)
        print(f"{y}: {len(hy)} filings mention PDUFA", flush=True)
        hits += hy

    # 1. mentions -> events (company + PDUFA date)
    events = {}
    with ThreadPoolExecutor(6) as pool:
        futs = {pool.submit(pdufa_mentions, h): h for h in hits}
        for i, f in enumerate(as_completed(futs), 1):
            h = futs[f]
            for m in f.result() or []:
                k = (h["cik"], m["pdufa"])
                e = events.setdefault(k, {"cik": h["cik"], "ticker": h["ticker"], "company": h["company"], "pdufa": m["pdufa"],
                                          "first_seen": h["date"], "last_seen": h["date"], "drugs": Counter(),
                                          "feats": Counter(), "n_filings": 0, "sentence": m["sentence"]})
                e["first_seen"] = min(e["first_seen"], h["date"]); e["last_seen"] = max(e["last_seen"], h["date"])
                e["n_filings"] += 1
                if m["drug"]:
                    e["drugs"][m["drug"].lower()] += 1
                for fk, v in m["feats"].items():
                    e["feats"][fk] = max(e["feats"][fk], v)
            if i % 500 == 0:
                print(f"  read {i}/{len(hits)} filings, {len(events)} events", flush=True)
    print(f"{len(events)} company + PDUFA-date events")

    # 2. outcomes (only for decisions that should have happened by now)
    today = date.today().isoformat()
    past = [e for e in events.values() if e["pdufa"] < today]
    by_cik = defaultdict(list)
    for e in past:
        e["drug"] = e["drugs"].most_common(1)[0][0] if e["drugs"] else None
        by_cik[e["cik"]].append(e)

    def label_company(cik):
        fl = company_filings(cik)
        for e in by_cik[cik]:
            e["outcome"], e["outcome_date"] = outcome_for(e, fl)
        return cik

    with ThreadPoolExecutor(6) as pool:
        for i, f in enumerate(as_completed([pool.submit(label_company, c) for c in by_cik]), 1):
            f.result()
            if i % 100 == 0:
                print(f"  labeled {i}/{len(by_cik)} companies", flush=True)

    # 3. sponsor track record known BEFORE each decision
    approvals_before = defaultdict(list)
    for e in past:
        if e.get("outcome") == "approved":
            approvals_before[e["cik"]].append(e["outcome_date"] or e["pdufa"])
    for e in events.values():
        e["sponsor_prior_approvals"] = sum(1 for d in approvals_before.get(e["cik"], []) if d < e["first_seen"])

    cols = ["cik", "ticker", "company", "pdufa", "first_seen", "last_seen", "n_filings", "drug", "outcome", "outcome_date",
            "sponsor_prior_approvals", *FEATURES.keys(), "sentence"]
    with open(OUTDIR / "history.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for e in sorted(events.values(), key=lambda e: (e["pdufa"], e["cik"])):
            w.writerow([e["cik"], e["ticker"], e["company"], e["pdufa"], e["first_seen"], e["last_seen"], e["n_filings"],
                        e.get("drug") or (e["drugs"].most_common(1)[0][0] if e["drugs"] else ""), e.get("outcome") or "",
                        e.get("outcome_date") or "", e["sponsor_prior_approvals"], *[e["feats"].get(k, 0) for k in FEATURES],
                        e["sentence"]])
    oc = Counter(e.get("outcome") for e in past)
    print("outcomes:", dict(oc))
    print(f"wrote {OUTDIR / 'history.csv'}")


if __name__ == "__main__":
    build._auth()
    main()
