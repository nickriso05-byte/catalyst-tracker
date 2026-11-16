"""
odds_export.py: compact files the live app needs to score upcoming catalysts (committed to the repo):
  odds/live/readout_model.json   trial-readout model + phase calibration
  odds/live/readout_history.json track records as of today (by phase x area, phase x endpoint, sponsor, drug)
  odds/live/fda_model.json       FDA-decision model (both layers)
  odds/live/sponsor_approvals.json  original FDA approvals per company
Run after odds_readout.py / odds_fda_v2.py / odds_fda_records.py.
"""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from pathlib import Path

import odds_readout as ro

HERE = Path(__file__).parent
LIVE = HERE / "odds" / "live"
LIVE.mkdir(parents=True, exist_ok=True)


def main():
    trials, labels, hl = ro.load()
    rows = ro.build_rows(trials, labels, hl)
    lab = [r for r in rows if r["y"] is not None and r["end"] is not None]
    agg = {k: defaultdict(lambda: [0, 0]) for k in ("pa", "pe", "sp", "dr")}
    g = [0, 0]
    for r in lab:
        for k, key in (("pa", f"{r['phase']}|{r['area']}"), ("pe", f"{r['phase']}|{r['endpoint']}"), ("sp", r["sponsor"]), ("dr", r["drug"])):
            if key:
                a = agg[k][key]; a[0] += r["y"]; a[1] += 1
        g[0] += r["y"]; g[1] += 1
    hist = {"global": g, **{k: {kk: v for kk, v in d.items()} for k, d in agg.items()}}
    (LIVE / "readout_history.json").write_text(json.dumps(hist, separators=(",", ":")), encoding="utf-8")
    shutil.copy(HERE / "odds" / "readout_model.json", LIVE / "readout_model.json")
    shutil.copy(HERE / "odds" / "fda_v2_model.json", LIVE / "fda_model.json")
    shutil.copy(HERE / "odds" / "sponsor_approvals.json", LIVE / "sponsor_approvals.json")
    for p in sorted(LIVE.iterdir()):
        print(f"{p.name:26s} {p.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
