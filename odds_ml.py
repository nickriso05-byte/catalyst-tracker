"""
odds_ml.py: the small amount of machine learning the odds models need, in plain NumPy
(scikit-learn's compiled parts are blocked by this machine's application-control policy).

- Regularized logistic regression fit by Newton's method (IRLS)
- 5-fold cross-validation to pick the regularization strength
- Backtest metrics: AUC, Brier score, log loss, calibration bins
"""
from __future__ import annotations

import numpy as np


class Scaler:
    def fit(self, X):
        self.mu = X.mean(0)
        self.sd = X.std(0)
        self.sd[self.sd == 0] = 1.0
        return self

    def transform(self, X):
        return (X - self.mu) / self.sd


def fit_logreg(X, y, l2=1.0, iters=60, w0=None):
    """Logistic regression with an L2 penalty (intercept not penalized). Returns (w, b)."""
    n, k = X.shape
    Xb = np.hstack([np.ones((n, 1)), X])
    beta = np.zeros(k + 1) if w0 is None else w0.copy()
    pen = np.full(k + 1, l2); pen[0] = 0.0
    for _ in range(iters):
        z = np.clip(Xb @ beta, -30, 30)
        p = 1 / (1 + np.exp(-z))
        g = Xb.T @ (p - y) + pen * beta
        W = p * (1 - p)
        H = (Xb * W[:, None]).T @ Xb + np.diag(pen + 1e-9)
        step = np.linalg.solve(H, g)
        beta -= step
        if np.abs(step).max() < 1e-7:
            break
    return beta[1:], beta[0]


def predict(X, w, b):
    return 1 / (1 + np.exp(-np.clip(X @ w + b, -30, 30)))


def auc(y, p):
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p)); ranks[order] = np.arange(1, len(p) + 1)
    # average ranks for ties
    _, inv, counts = np.unique(p, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    ranks = (sums / counts)[inv]
    n1 = y.sum(); n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def brier(y, p):
    return float(np.mean((p - y) ** 2))


def logloss(y, p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def pick_l2(X, y, grid=(0.3, 1, 3, 10, 30, 100), folds=5, seed=0):
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(y))
    parts = np.array_split(idx, folds)
    best, best_l2 = 1e9, grid[0]
    for l2 in grid:
        losses = []
        for i in range(folds):
            te = parts[i]; tr = np.concatenate([parts[j] for j in range(folds) if j != i])
            sc = Scaler().fit(X[tr])
            w, b = fit_logreg(sc.transform(X[tr]), y[tr], l2)
            losses.append(logloss(y[te], predict(sc.transform(X[te]), w, b)))
        m = float(np.mean(losses))
        if m < best:
            best, best_l2 = m, l2
    return best_l2


def report(y, p, base, out):
    pb = np.full_like(p, base)
    out(f"  AUC        model {auc(y, p):.3f}   baseline 0.500")
    out(f"  Brier      model {brier(y, p):.4f}   baseline {brier(y, pb):.4f}")
    out(f"  Log loss   model {logloss(y, p):.4f}   baseline {logloss(y, pb):.4f}")
    order = np.argsort(p); n = len(p)
    for label, sl in (("Lowest-odds 20%", order[: n // 5]), ("Middle 60%", order[n // 5: n - n // 5]), ("Highest-odds 20%", order[n - n // 5:])):
        out(f"  {label:17s} predicted {p[sl].mean():.1%}   actual {y[sl].mean():.1%}   (n={len(sl)})")
