"""
odds_fda_model.py: FDA-decision odds in two layers.

Layer 1, the FDA record (odds/fda_decisions.csv: Drugs@FDA approvals + FDA's published CRLs, 2016 onward).
  Uses only facts known for EVERY decision, approved or not: biologic or drug, new drug or new use (supplement),
  resubmission after a CRL, the sponsor's prior approvals, cancer drug or not.
  (Review priority / NME status exist only for drugs that were eventually approved, so they'd leak the answer here.)
Layer 2, the company's own filings (odds/history.csv, SEC 8-K/6-K history).
  Adds what companies disclose before the decision: priority review, breakthrough, fast track, orphan, accelerated
  approval, advisory committee plans and votes, review extensions, manufacturing issues, label talks.
  Fit on top of layer 1's estimate (layer 1's log-odds is an input).
Backtest: train before 2023, test 2023 onward (layer 1); train before 2021, test 2021 onward (layer 2).
Writes odds/fda_model.json and odds/fda_backtest.txt.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from odds_ml import Scaler, auc, brier, fit_logreg, logloss, pick_l2, predict, report
from odds_history import FEATURES as SEC_FEATURES

HERE = Path(__file__).parent
L1 = ["bla", "supplemental", "resubmission", "onc", "sponsor_has_approval", "sponsor_log_approvals"]
L2_SEC = [f for f in SEC_FEATURES if f not in ("bla", "supplemental", "resubmission", "prior_crl", "partnered")]
L2 = ["l1_logit"] + L2_SEC


def rows_l1():
    rows = list(csv.DictReader(open(HERE / "odds" / "fda_decisions.csv", encoding="utf-8")))
    for r in rows:
        n = int(r["sponsor_prior_approvals"] or 0)
        r["sponsor_has_approval"] = float(n > 0); r["sponsor_log_approvals"] = math.log1p(n)
        for f in ("bla", "supplemental", "resubmission", "onc"):
            r[f] = float(r[f] or 0)
        r["y"] = float(r["y"])
    return rows


def rows_l2():
    out = []
    for r in csv.DictReader(open(HERE / "odds" / "history.csv", encoding="utf-8")):
        if r["outcome"] not in ("approved", "crl"):
            continue
        n = int(r["sponsor_prior_approvals"] or 0)
        x = {"date": r["pdufa"], "y": 1.0 if r["outcome"] == "approved" else 0.0,
             "bla": float(r["bla"]), "supplemental": float(r["supplemental"]),
             "resubmission": float(r["resubmission"] == "1" or r["prior_crl"] == "1"), "onc": 0.0,
             "sponsor_has_approval": float(n > 0), "sponsor_log_approvals": math.log1p(n)}
        for f in L2_SEC:
            x[f] = float(r[f])
        out.append(x)
    return out


def M(rows, feats):
    return np.array([[r[f] for f in feats] for r in rows], dtype=float), np.array([r["y"] for r in rows])


def main():
    lines = []; out = lines.append
    # ---------------- layer 1
    R = rows_l1()
    tr = [r for r in R if r["date"] < "2023-01-01"]; te = [r for r in R if r["date"] >= "2023-01-01"]
    Xtr, ytr = M(tr, L1); Xte, yte = M(te, L1)
    l2a = pick_l2(Xtr, ytr)
    sc1 = Scaler().fit(Xtr); w1, b1 = fit_logreg(sc1.transform(Xtr), ytr, l2a)
    p1 = predict(sc1.transform(Xte), w1, b1)
    out(f"LAYER 1 (FDA records): {len(R):,} decisions since 2016, {int(sum(r['y'] for r in R)):,} approvals / {int(len(R) - sum(r['y'] for r in R)):,} CRLs")
    out(f"  train <2023: {len(tr):,} (approved {ytr.mean():.1%})   test 2023+: {len(te):,} (approved {yte.mean():.1%})")
    report(yte, p1, ytr.mean(), out)
    out("  weights: " + ", ".join(f"{f} {w:+.2f}" for f, w in sorted(zip(L1, w1), key=lambda x: -abs(x[1]))))
    # refit layer 1 on all for use as an input to layer 2 and for live scoring
    Xa, ya = M(R, L1); sca = Scaler().fit(Xa); wa, ba = fit_logreg(sca.transform(Xa), ya, l2a)
    c1 = (wa / sca.sd); i1 = float(ba - (wa * sca.mu / sca.sd).sum())

    # ---------------- layer 2
    S = rows_l2()
    for r in S:
        r["l1_logit"] = float(np.dot(c1, [r[f] for f in L1]) + i1)
    tr2 = [r for r in S if r["date"] < "2021-01-01"]; te2 = [r for r in S if r["date"] >= "2021-01-01"]
    X2, y2 = M(tr2, L2); Xt2, yt2 = M(te2, L2)
    lb = pick_l2(X2, y2, grid=(3, 10, 30, 100))
    sc2 = Scaler().fit(X2); w2, b2 = fit_logreg(sc2.transform(X2), y2, lb)
    p2 = predict(sc2.transform(Xt2), w2, b2)
    pl1 = 1 / (1 + np.exp(-Xt2[:, 0]))
    out(""); out(f"LAYER 2 (+ company filings): {len(S):,} decisions with SEC history; train <2021: {len(tr2)}, test 2021+: {len(te2)} (approved {yt2.mean():.1%})")
    out(f"  Layer 1 alone on this test set:  AUC {auc(yt2, pl1):.3f}   Brier {brier(yt2, pl1):.4f}")
    report(yt2, p2, y2.mean(), out)
    out("  top weights: " + ", ".join(f"{f} {w:+.2f}" for f, w in sorted(zip(L2, w2), key=lambda x: -abs(x[1]))[:12]))
    X2a, y2a = M(S, L2); sc2a = Scaler().fit(X2a); w2a, b2a = fit_logreg(sc2a.transform(X2a), y2a, lb)
    (HERE / "odds" / "fda_model.json").write_text(json.dumps({
        "layer1": {"features": L1, "coef": c1.tolist(), "intercept": i1, "n": len(R)},
        "layer2": {"features": L2, "coef": (w2a / sc2a.sd).tolist(), "intercept": float(b2a - (w2a * sc2a.mu / sc2a.sd).sum()), "n": len(S)},
        "base_rate": float(np.mean([r["y"] for r in R])),
        "backtest": {"layer1_auc": auc(yte, p1), "layer2_auc": auc(yt2, p2), "layer1_on_l2_test_auc": auc(yt2, pl1)}}, indent=1), encoding="utf-8")
    txt = "\n".join(lines)
    (HERE / "odds" / "fda_backtest.txt").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
