"""
odds_fda_v2.py: FDA-decision odds, expanded.

1. More labeled decisions: SEC-history PDUFA events with no outcome in the filings get one from the FDA's own
   records (Drugs@FDA approvals / published CRLs) when exactly one FDA decision for that company falls near the
   PDUFA date. Where both sources have a label, their agreement rate is reported as a label-quality check.
2. Consistent sponsor track record: original approvals per company (all years, Drugs@FDA), matched by one company key.
3. Pivotal-trial link: each decision is matched to the company's late-stage trial of that drug on ClinicalTrials.gov
   (finished before the decision), adding design inputs: size, randomization, blinding, comparator, endpoint, area.
4. Layer-1 prior: the FDA-records model's log-odds (odds/fda_model.json) is an input.
Train before 2021, test 2021 onward. Writes odds/fda_v2_model.json and odds/fda_v2_backtest.txt.
"""
from __future__ import annotations

import csv
import json
import math
import re
from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from odds_fda_records import sponsor_key
from odds_history import FEATURES as SEC_FEATURES
from odds_ml import Scaler, auc, brier, fit_logreg, pick_l2, predict, report

HERE = Path(__file__).parent
ONCO = re.compile(r"cancer|tumou?r|carcinoma|lymphoma|leukemia|myeloma|melanoma|sarcoma|oncolog|metasta|neoplas", re.I)
FILING = [f for f in SEC_FEATURES if f not in ("prior_crl",)]
PIVOTAL = ["piv_linked", "piv_randomized", "piv_placebo", "piv_active", "piv_masking", "piv_single_arm", "piv_log_n",
           "piv_ep_survival", "piv_ep_response", "piv_ep_symptom", "piv_ep_biomarker", "piv_phase3"]
FEATS = ["l1_logit", "resub", "sponsor_has_approval", "sponsor_log_approvals", "onc"] + FILING + PIVOTAL


def load_trials_by_sponsor():
    by = defaultdict(list)
    with open(HERE / "odds" / "trials.jsonl", encoding="utf-8") as fh:
        for line in fh:
            t = json.loads(line)
            if t.get("phase") not in ("PHASE3", "PHASE2/PHASE3", "PHASE2") or not t.get("start_year"):
                continue
            k = sponsor_key(t.get("sponsor"))
            if k:
                by[k].append(t)
    return by


def pivotal_for(trials, drug, pdufa_year):
    """The company's latest late-stage trial of this drug that finished before the decision year."""
    if not trials:
        return None
    d = (drug or "").lower()
    cands = []
    for t in trials:
        end = t["start_year"] + (t.get("duration_years") or 2)
        if end > pdufa_year or end < pdufa_year - 6:
            continue
        names = " ".join(t.get("interventions") or []).lower()
        if d and len(d) >= 4 and (d in names or any(n and len(n) >= 4 and n in d for n in names.split())):
            cands.append((t["phase"] == "PHASE3", end, t))
    if not cands:
        p3 = [t for t in trials if t["phase"] in ("PHASE3", "PHASE2/PHASE3") and pdufa_year - 5 <= t["start_year"] + (t.get("duration_years") or 2) <= pdufa_year]
        return p3[0] if len(p3) == 1 else None      # only when the company has exactly one recent pivotal trial
    return max(cands, key=lambda c: (c[0], c[1]))[2]


def piv_features(t):
    if not t:
        return {f: 0.0 for f in PIVOTAL}
    ep = t.get("endpoint")
    return {"piv_linked": 1.0, "piv_randomized": float(t.get("randomized") or 0), "piv_placebo": float(t.get("placebo_arm") or 0),
            "piv_active": float(t.get("active_comparator") or 0), "piv_masking": float(t.get("masking_level") or 0),
            "piv_single_arm": float(t.get("single_arm") or 0), "piv_log_n": float(t.get("log_enrollment") or 0),
            "piv_ep_survival": float(ep in ("overall_survival", "progression_free", "clinical_event")),
            "piv_ep_response": float(ep == "response_rate"), "piv_ep_symptom": float(ep == "symptom_score"),
            "piv_ep_biomarker": float(ep == "biomarker"), "piv_phase3": float(t.get("phase") in ("PHASE3", "PHASE2/PHASE3"))}


def main():
    sec = list(csv.DictReader(open(HERE / "odds" / "history.csv", encoding="utf-8")))
    fda = list(csv.DictReader(open(HERE / "odds" / "fda_decisions.csv", encoding="utf-8")))
    spa = json.loads((HERE / "odds" / "sponsor_approvals.json").read_text(encoding="utf-8"))
    m1 = json.loads((HERE / "odds" / "fda_model.json").read_text(encoding="utf-8"))["layer1"]
    trials = load_trials_by_sponsor()

    fda_by = defaultdict(list)
    for f in fda:
        k = sponsor_key(f["sponsor"])
        if k:
            fda_by[k].append(f)

    today = date.today().isoformat()
    rows, agree, disagree, added = [], 0, 0, 0
    for r in sec:
        if r["pdufa"] >= today:
            continue
        k = sponsor_key(r["company"])
        pd = date.fromisoformat(r["pdufa"])
        lo, hi = (pd - timedelta(days=45)).isoformat(), (pd + timedelta(days=21)).isoformat()
        near = [f for f in fda_by.get(k, []) if lo <= f["date"] <= hi]
        fda_label = None
        if len(near) == 1:
            fda_label = "approved" if near[0]["y"] == "1" else "crl"
        sec_label = r["outcome"] if r["outcome"] in ("approved", "crl") else None
        if sec_label and fda_label:
            agree += sec_label == fda_label; disagree += sec_label != fda_label
            if sec_label != fda_label:
                continue                      # sources conflict: leave it out
        label = sec_label or fda_label
        if not label:
            continue
        added += int(not sec_label)
        x = {"date": r["pdufa"], "y": 1.0 if label == "approved" else 0.0, "ticker": r["ticker"], "company": r["company"]}
        appr = spa.get(k or "", [])
        n = bisect_left(appr, r["first_seen"])            # approvals before the company first named this date
        x["sponsor_has_approval"], x["sponsor_log_approvals"] = float(n > 0), math.log1p(n)
        x["resub"] = float(r["resubmission"] == "1" or r["prior_crl"] == "1")
        x["onc"] = float(bool(ONCO.search((r["sentence"] or "") + " " + (r["drug"] or ""))))
        for f in FILING:
            x[f] = float(r[f])
        piv = pivotal_for(trials.get(k or ""), r["drug"], pd.year)
        x.update(piv_features(piv))
        if piv and piv.get("area") == "oncology":
            x["onc"] = 1.0
        l1 = {"bla": x["bla"], "supplemental": x["supplemental"], "resubmission": x["resub"], "onc": x["onc"],
              "sponsor_has_approval": x["sponsor_has_approval"], "sponsor_log_approvals": x["sponsor_log_approvals"]}
        x["l1_logit"] = float(np.dot(m1["coef"], [l1[f] for f in m1["features"]]) + m1["intercept"])
        rows.append(x)

    lines = []; out = lines.append
    out(f"Labeled FDA decisions with company-filing signals: {len(rows):,} (was 566; {added:,} added from FDA records)")
    out(f"Label check where both sources label the same decision: {agree} agree, {disagree} disagree "
        f"({agree / max(1, agree + disagree):.0%} agreement); conflicts are dropped")
    out(f"Linked to a pivotal trial on ClinicalTrials.gov: {sum(r['piv_linked'] for r in rows):,.0f}")
    tr = [r for r in rows if r["date"] < "2021-01-01"]; te = [r for r in rows if r["date"] >= "2021-01-01"]
    X = lambda rs: np.array([[r[f] for f in FEATS] for r in rs]); Y = lambda rs: np.array([r["y"] for r in rs])
    Xtr, ytr, Xte, yte = X(tr), Y(tr), X(te), Y(te)
    l2 = pick_l2(Xtr, ytr, grid=(3, 10, 30, 100, 300))
    sc = Scaler().fit(Xtr); w, b = fit_logreg(sc.transform(Xtr), ytr, l2)
    p = predict(sc.transform(Xte), w, b)
    out(""); out(f"Train <2021: {len(tr):,} (approved {ytr.mean():.1%})   Test 2021+: {len(te):,} (approved {yte.mean():.1%})   L2={l2}")
    p1 = 1 / (1 + np.exp(-Xte[:, 0]))
    out(f"  FDA-records prior alone   AUC {auc(yte, p1):.3f}   Brier {brier(yte, p1):.4f}")
    report(yte, p, ytr.mean(), out)
    out("  Calibration (predicted vs actual, test):")
    for lo_, hi_ in ((0, .7), (.7, .8), (.8, .9), (.9, 1.01)):
        sel = (p >= lo_) & (p < hi_)
        if sel.sum():
            out(f"    {lo_:.0%}-{min(hi_, 1):.0%}: predicted {p[sel].mean():.1%}  actual {yte[sel].mean():.1%}  n={sel.sum()}")
    out(""); out("Inputs that move FDA-decision odds most (standardized weight):")
    for f, wt in sorted(zip(FEATS, w), key=lambda z: -abs(z[1]))[:20]:
        out(f"  {f:26s} {wt:+.2f}")

    Xa, ya = X(rows), Y(rows); sca = Scaler().fit(Xa); wa, ba = fit_logreg(sca.transform(Xa), ya, l2)
    (HERE / "odds" / "fda_v2_model.json").write_text(json.dumps({
        "features": FEATS, "coef": (wa / sca.sd).tolist(), "intercept": float(ba - (wa * sca.mu / sca.sd).sum()),
        "layer1": m1, "n": len(rows), "base_rate": float(ya.mean()),
        "backtest": {"auc": auc(yte, p), "brier": brier(yte, p), "brier_base": brier(yte, np.full_like(p, ytr.mean())), "n_test": len(te)}}, indent=1), encoding="utf-8")
    txt = "\n".join(lines)
    (HERE / "odds" / "fda_v2_backtest.txt").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
