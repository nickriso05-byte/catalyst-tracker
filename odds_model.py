"""
odds_model.py: train and backtest the FDA-decision odds model on odds/history.csv.

Train on decisions before 2020, test on 2020 onward (the model never sees the test years).
Compared against the baseline "every drug gets the historical approval rate".
Writes odds/model.json (plain logistic-regression weights, so the app needs no ML library) and odds/backtest.txt.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np

from odds_ml import Scaler, auc, brier, fit_logreg, logloss, pick_l2, predict, report

from odds_history import FEATURES

HERE = Path(__file__).parent
SPLIT = "2020-01-01"
FEATS = list(FEATURES) + ["sponsor_has_approval", "sponsor_prior_approvals_log"]


def load():
    rows = [r for r in csv.DictReader(open(HERE / "odds" / "history.csv", encoding="utf-8")) if r["outcome"] in ("approved", "crl")]
    for r in rows:
        r["y"] = 1 if r["outcome"] == "approved" else 0
        n = int(r["sponsor_prior_approvals"] or 0)
        r["sponsor_has_approval"] = 1 if n > 0 else 0
        r["sponsor_prior_approvals_log"] = math.log1p(n)
    return rows


def matrix(rows):
    return np.array([[float(r[f]) for f in FEATS] for r in rows]), np.array([r["y"] for r in rows])


def main():
    rows = load()
    train = [r for r in rows if r["pdufa"] < SPLIT]
    test = [r for r in rows if r["pdufa"] >= SPLIT]
    Xtr, ytr = matrix(train)
    Xte, yte = matrix(test)
    base = ytr.mean()

    l2 = pick_l2(Xtr, ytr)
    sc = Scaler().fit(Xtr)
    w, b = fit_logreg(sc.transform(Xtr), ytr, l2)
    p = predict(sc.transform(Xte), w, b)

    lines = []
    out = lines.append
    out(f"Decisions labeled: {len(rows)}  (train <{SPLIT}: {len(train)}, test: {len(test)})")
    out(f"Approval rate: train {ytr.mean():.1%}, test {yte.mean():.1%}")
    out(f"Regularization L2 = {l2}")
    out("")
    out("Backtest on the test years (model never saw them):")
    report(yte, p, base, out)
    out("")
    out("What moves the odds (standardized weight; + raises approval odds, - lowers):")
    for f, wt in sorted(zip(FEATS, w), key=lambda x: -abs(x[1])):
        out(f"  {f:28s} {wt:+.2f}")

    # refit on everything for live use; store raw-scale weights so the app needs only arithmetic
    Xall, yall = matrix(rows)
    sca = Scaler().fit(Xall)
    wa, ba = fit_logreg(sca.transform(Xall), yall, l2)
    coef = wa / sca.sd
    intercept = ba - float((wa * sca.mu / sca.sd).sum())
    (HERE / "odds" / "model.json").write_text(json.dumps({
        "features": FEATS, "coef": coef.tolist(), "intercept": intercept,
        "base_rate": float(yall.mean()), "n": len(rows), "trained_through": max(r["pdufa"] for r in rows),
        "backtest": {"auc": auc(yte, p), "brier": brier(yte, p), "brier_base": brier(yte, np.full_like(p, base)),
                     "n_test": len(test), "split": SPLIT}}, indent=1), encoding="utf-8")
    txt = "\n".join(lines)
    (HERE / "odds" / "backtest.txt").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
