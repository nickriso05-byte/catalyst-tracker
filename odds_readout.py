"""
odds_readout.py: trial-readout odds model (does a trial succeed?), trained on ClinicalTrials.gov designs
(odds/trials.jsonl) and CTO outcome labels (odds/cto/*.csv).

Leak-free by construction:
  - no outcome-era fields (status, actual enrollment, completion date) as inputs;
  - drug, sponsor and precedent histories only count trials that FINISHED BEFORE this one started.
Tests:
  A. trials finishing 2017-2019 (CTO labels), never seen in training;
  B. the 11,012 hand-labeled trials from 2020-2024 (the cleanest labels), never seen in training.
Compared against the historical-average method: success rate by phase, and by phase x disease area.
Writes odds/readout_model.json and odds/readout_backtest.txt.
"""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from odds_ml import Scaler, auc, brier, fit_logreg, logloss, pick_l2, predict, report

HERE = Path(__file__).parent
CTO = HERE / "odds" / "cto"
TRAIN_END = 2016          # train on trials finishing <= 2016
TEST_A = (2017, 2019)

PHASES = ["PHASE1", "PHASE1/PHASE2", "PHASE2", "PHASE2/PHASE3", "PHASE3", "PHASE4"]
ENDPOINTS = ["overall_survival", "progression_free", "response_rate", "clinical_event", "symptom_score", "biomarker", "pk_safety", "other"]
AREAS = ["oncology", "neuro_psych", "cardio_metabolic", "infectious", "immunology", "respiratory", "rare_genetic", "other"]
DESIGN = ["randomized", "masking_level", "placebo_arm", "active_comparator", "single_arm", "n_arms_c", "n_primary_c",
          "biologic", "genetic", "industry_sponsor", "has_collaborator"]
HIST = ["prec_phase_area", "prec_phase_endpoint", "sponsor_log_n", "sponsor_rate", "sponsor_first", "drug_log_n", "drug_rate", "drug_prior_success"]
FEATS = DESIGN + [f"ph_{p}" for p in PHASES] + [f"ep_{e}" for e in ENDPOINTS] + [f"ar_{a}" for a in AREAS] + ["start_c"] + HIST

DOSE = re.compile(r"\b\d+(\.\d+)?\s*(mg|mcg|µg|ug|g|ml|iu|units?|%)\b.*|\(.*?\)|tablets?|capsules?|injection|infusion|oral|iv\b|sc\b", re.I)


def drug_key(names):
    for n in names or []:
        n = (n or "").lower()
        if not n or "placebo" in n or "standard of care" in n or "vehicle" in n:
            continue
        k = DOSE.sub("", n).strip(" -,")
        if len(k) >= 3:
            return k
    return None


def load():
    trials = {}
    with open(HERE / "odds" / "trials.jsonl", encoding="utf-8") as fh:
        for line in fh:
            t = json.loads(line)
            if t.get("nct_id"):
                trials[t["nct_id"]] = t
    labels = {}
    for f in ("phase1_CTO_rf.csv", "phase2_CTO_rf.csv", "phase3_CTO_rf.csv"):
        d = pd.read_csv(CTO / f, usecols=["nct_id", "pred_proba"])
        for n, p in zip(d.nct_id, d.pred_proba):
            labels[n] = int(p >= 0.5)
    human = pd.read_csv(CTO / "human_labels_2020_2024.csv", usecols=["nct_id", "labels"]).dropna()
    hl = {n: int(v) for n, v in zip(human.nct_id, human.labels)}
    return trials, labels, hl


def history_features(rows):
    """Precedent / sponsor / drug track records using only trials that finished before each trial started."""
    # completions sorted by year; queries by start year
    done = sorted([r for r in rows if r.get("end") is not None and r.get("y") is not None], key=lambda r: r["end"])
    qs = sorted(rows, key=lambda r: r["start"] or 0)
    agg = {k: defaultdict(lambda: [0, 0]) for k in ("pa", "pe", "sp", "dr")}
    glob = [0, 0]
    i = 0
    for r in qs:
        s = r["start"] or 0
        while i < len(done) and done[i]["end"] < s:      # strictly earlier finishers only
            d = done[i]; y = d["y"]
            for k, key in (("pa", (d["phase"], d["area"])), ("pe", (d["phase"], d["endpoint"])), ("sp", d["sponsor"]), ("dr", d["drug"])):
                if key is not None and key != (None, None):
                    a = agg[k][key]; a[0] += y; a[1] += 1
            glob[0] += y; glob[1] += 1
            i += 1
        g = (glob[0] + 1) / (glob[1] + 2)
        def rate(k, key, m=10):
            a = agg[k].get(key, [0, 0]) if key is not None else [0, 0]
            return (a[0] + m * g) / (a[1] + m), a[1]
        r["prec_phase_area"], _ = rate("pa", (r["phase"], r["area"]))
        r["prec_phase_endpoint"], _ = rate("pe", (r["phase"], r["endpoint"]))
        sr, sn = rate("sp", r["sponsor"], 5)
        r["sponsor_rate"], r["sponsor_log_n"], r["sponsor_first"] = sr, math.log1p(sn), int(sn == 0)
        dr, dn = rate("dr", r["drug"], 3)
        da = agg["dr"].get(r["drug"], [0, 0]) if r["drug"] else [0, 0]
        r["drug_rate"], r["drug_log_n"], r["drug_prior_success"] = dr, math.log1p(dn), int(da[0] > 0)


def build_rows(trials, labels, hl):
    rows = []
    for nct, t in trials.items():
        if t.get("start_year") is None:
            continue
        y = hl.get(nct, labels.get(nct))
        r = {"nct": nct, "y": y, "human": nct in hl, "start": t["start_year"],
             "end": (t["start_year"] + t["duration_years"]) if t.get("duration_years") is not None else None,
             "phase": t.get("phase") or "", "area": t.get("area") or "other", "endpoint": t.get("endpoint") or "other",
             "sponsor": (t.get("sponsor") or "").lower() or None, "drug": drug_key(t.get("interventions"))}
        for f in ("randomized", "masking_level", "placebo_arm", "active_comparator", "single_arm", "biologic", "genetic", "industry_sponsor", "has_collaborator"):
            r[f] = float(t.get(f) or 0)
        r["n_arms_c"] = float(min(t.get("n_arms") or 0, 6))
        r["n_primary_c"] = float(min(t.get("n_primary_outcomes") or 0, 5))
        for p in PHASES:
            r[f"ph_{p}"] = float(r["phase"] == p)
        for e in ENDPOINTS:
            r[f"ep_{e}"] = float(r["endpoint"] == e)
        for a in AREAS:
            r[f"ar_{a}"] = float(r["area"] == a)
        r["start_c"] = float(min(max(r["start"], 1995), 2026) - 2010) / 10
        rows.append(r)
    history_features(rows)
    return rows


def baseline_rates(train, key):
    agg = defaultdict(lambda: [0, 0])
    for r in train:
        a = agg[key(r)]; a[0] += r["y"]; a[1] += 1
    g = sum(r["y"] for r in train) / len(train)
    return lambda r: (agg[key(r)][0] + 10 * g) / (agg[key(r)][1] + 10) if key(r) in agg else g


def main():
    trials, labels, hl = load()
    rows = build_rows(trials, labels, hl)
    lab = [r for r in rows if r["y"] is not None and r["phase"] in PHASES]
    train = [r for r in lab if not r["human"] and r["end"] is not None and r["end"] <= TRAIN_END]
    testA = [r for r in lab if not r["human"] and r["end"] is not None and TEST_A[0] <= r["end"] <= TEST_A[1]]
    testB = [r for r in lab if r["human"]]
    X = lambda rs: np.array([[r[f] for f in FEATS] for r in rs], dtype=float)
    Y = lambda rs: np.array([r["y"] for r in rs], dtype=float)
    Xtr, ytr = X(train), Y(train)
    l2 = pick_l2(Xtr, ytr, grid=(1, 3, 10, 30, 100))
    sc = Scaler().fit(Xtr)
    w, b = fit_logreg(sc.transform(Xtr), ytr, l2)

    by_phase = baseline_rates(train, lambda r: r["phase"])
    by_phase_area = baseline_rates(train, lambda r: (r["phase"], r["area"]))
    lines = []; out = lines.append
    out(f"Trials with designs: {len(rows):,}; labeled: {len(lab):,}")
    out(f"Train (finished <= {TRAIN_END}): {len(train):,} trials, success rate {ytr.mean():.1%}   L2={l2}")
    for name, te in ((f"Test A: finished {TEST_A[0]}-{TEST_A[1]} (CTO labels)", testA), ("Test B: 2020-2024 hand-labeled trials", testB)):
        Xt, yt = X(te), Y(te)
        p = predict(sc.transform(Xt), w, b)
        out(""); out(f"{name}: n={len(te):,}, success rate {yt.mean():.1%}")
        out("  Historical average by phase           AUC %.3f   Brier %.4f" % (auc(yt, np.array([by_phase(r) for r in te])), brier(yt, np.array([by_phase(r) for r in te]))))
        out("  Historical average by phase x area    AUC %.3f   Brier %.4f" % (auc(yt, np.array([by_phase_area(r) for r in te])), brier(yt, np.array([by_phase_area(r) for r in te]))))
        out("  Model (all inputs)                    AUC %.3f   Brier %.4f" % (auc(yt, p), brier(yt, p)))
        report(yt, p, ytr.mean(), out)
    out(""); out("Inputs that move the odds most (standardized weight):")
    for f, wt in sorted(zip(FEATS, w), key=lambda x: -abs(x[1]))[:25]:
        out(f"  {f:24s} {wt:+.2f}")

    # final model on everything labeled (not the hand-labeled exam set) for live use
    allr = [r for r in lab if not r["human"]]
    sca = Scaler().fit(X(allr))
    wa, ba = fit_logreg(sca.transform(X(allr)), Y(allr), l2)
    coef = wa / sca.sd
    intercept = float(ba - (wa * sca.mu / sca.sd).sum())

    # Calibration: the CTO labels call ~84% of trials successes, so raw odds run high. Anchor each phase to the
    # industry average (BIO/Informa 2011-2020, odds a drug advances from that phase) and keep the model's
    # trial-by-trial differences: shift each phase so its average industry trial since 2010 lands on that rate.
    BIO = {"PHASE1": 0.520, "PHASE1/PHASE2": 0.520, "PHASE2": 0.289, "PHASE2/PHASE3": 0.289, "PHASE3": 0.578}
    offsets = {}
    for ph, target in BIO.items():
        zs = np.array([np.dot(coef, [r[f] for f in FEATS]) + intercept for r in allr
                       if r["phase"] == ph and r["industry_sponsor"] == 1 and r["start"] >= 2010])
        if len(zs) < 50:
            continue
        lo, hi = -10.0, 10.0
        for _ in range(60):
            mid = (lo + hi) / 2
            if (1 / (1 + np.exp(-(zs + mid)))).mean() > target:
                hi = mid
            else:
                lo = mid
        offsets[ph] = round((lo + hi) / 2, 4)
        zc = zs + offsets[ph]
        p = 1 / (1 + np.exp(-zc))
        out(f"  Calibrated {ph:14s} mean {p.mean():.1%} (industry rate {target:.1%}); 10th-90th pct {np.percentile(p,10):.0%}-{np.percentile(p,90):.0%}")
    (HERE / "odds" / "readout_model.json").write_text(json.dumps({
        "features": FEATS, "coef": coef.tolist(), "intercept": intercept, "phase_offsets": offsets,
        "calibration": "phase averages anchored to BIO/Informa 2011-2020 phase-transition rates",
        "n_train": len(allr), "trained_through": max(r["end"] or 0 for r in allr)}, indent=1), encoding="utf-8")
    txt = "\n".join(lines)
    (HERE / "odds" / "readout_backtest.txt").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
