"""
odds_live.py: approval odds for upcoming PDUFA dates, using odds/model.json (trained by odds_model.py).

Signals come from the company's own 8-K / 6-K filings in the 14 months before the decision that mention
that PDUFA date, read exactly the way odds_history.py reads the historical ones.
"""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import build
import odds_history as oh

HERE = Path(__file__).parent

LABELS = {  # plain-English names for the card
    "priority_review": "Priority review", "breakthrough": "Breakthrough therapy", "fast_track": "Fast track",
    "orphan": "Orphan drug", "accelerated": "Accelerated approval pathway", "rtor_or_voucher": "Real-time review / priority voucher",
    "adcom_planned": "Advisory committee planned", "adcom_none": "No advisory committee", "adcom_positive": "Advisory committee voted in favor",
    "adcom_negative": "Advisory committee voted against", "resubmission": "Resubmission", "prior_crl": "Earlier rejection letter (CRL)",
    "supplemental": "Supplemental application (new use)", "bla": "Biologic (BLA)", "nda_505b2": "505(b)(2) application",
    "extended": "Review was extended", "manufacturing": "Manufacturing / inspection mentioned", "label_talks": "Label talks / late-cycle meeting",
    "partnered": "Partnered program", "sponsor_has_approval": "Company has gotten a drug approved before",
    "sponsor_prior_approvals_log": "Company's approval track record",
}


@lru_cache(maxsize=1)
def model():
    p = HERE / "odds" / "model.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


@lru_cache(maxsize=1)
def sponsor_approvals():
    """Approvals per company (CIK) from the history file."""
    out = defaultdict(int)
    p = HERE / "odds" / "history.csv"
    if p.exists():
        for r in csv.DictReader(open(p, encoding="utf-8")):
            if r["outcome"] == "approved":
                out[int(r["cik"])] += 1
    return out


def features(cik: int, pdufa: str, drug: str | None):
    """Signal flags from the company's filings that mention this PDUFA date (8-K/6-K, last ~14 months)."""
    pd = date.fromisoformat(pdufa)
    since = (pd - timedelta(days=420)).isoformat()
    feats = {k: 0 for k in oh.FEATURES}
    n = 0
    for f in oh.company_filings(cik):
        if not (since <= f["date"] < pd.isoformat()):
            continue
        sents = []
        for url in oh.filing_docs(cik, f):
            sents += oh.cached_sentences(url) or []
        mentions = any(oh.PDUFA_RX.search(s) and any(d["precision"] == "day" and d["start"] == pd for d in build.find_dates(s)) for s in sents)
        if not mentions:
            continue
        n += 1
        for k, rx in oh.FEAT_RX.items():
            if any(rx.search(s) for s in sents):
                feats[k] = 1
    k = sponsor_approvals().get(cik, 0)
    feats["sponsor_has_approval"] = 1 if k else 0
    feats["sponsor_prior_approvals_log"] = math.log1p(k)
    return feats, n


def score(cik: int, pdufa: str, drug: str | None = None):
    """{'p', 'base', 'drivers': [(label, effect)], 'filings'} or None if no model / no filings mention the date."""
    m = model()
    if not m or not cik:
        return None
    feats, n = features(cik, pdufa, drug)
    if n == 0:
        return None
    z = m["intercept"] + sum(w * float(feats.get(f, 0)) for f, w in zip(m["features"], m["coef"]))
    p = 1 / (1 + math.exp(-z))
    contrib = [(LABELS.get(f, f), w * float(feats.get(f, 0))) for f, w in zip(m["features"], m["coef"]) if feats.get(f)]
    contrib.sort(key=lambda x: -abs(x[1]))
    return {"p": round(p, 3), "base": round(m["base_rate"], 3), "filings": n,
            "drivers": [[l, round(c, 2)] for l, c in contrib[:6]],
            "model": {"n": m["n"], "auc": round(m["backtest"]["auc"], 2), "trained_through": m["trained_through"]}}
