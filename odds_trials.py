"""
odds_trials.py: trial-design features from ClinicalTrials.gov for every trial in the CTO label files.

Only facts fixed BEFORE a readout are kept (design, size, comparator, endpoints, disease area, sponsor),
so the readout model can't peek at the outcome.
Output: odds/trials.jsonl (one trial per line). Batches are cached, so re-runs are quick.
Run: python odds_trials.py
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
CTO = HERE / "odds" / "cto"
OUT = HERE / "odds" / "trials.jsonl"
CACHE = HERE / "cache" / "ctgov"
CACHE.mkdir(parents=True, exist_ok=True)
API = "https://clinicaltrials.gov/api/v2/studies"
FIELDS = ("NCTId,Phase,DesignInfo,EnrollmentInfo,ArmGroup,Intervention,PrimaryOutcome,Condition,LeadSponsor,"
          "StartDate,PrimaryCompletionDate,ConditionAncestorTerm,ConditionMeshTerm,OverallStatus,Keyword,Collaborator")

ENDPOINTS = [  # primary-endpoint type, roughly hardest to easiest to move
    ("overall_survival", r"overall survival|\bOS\b|all-cause mortality|death from any cause|mortality"),
    ("progression_free", r"progression[- ]free|event[- ]free|disease[- ]free|time to progression|\bPFS\b|\bEFS\b|\bDFS\b"),
    ("response_rate", r"response rate|objective response|\bORR\b|complete response|remission|responders?"),
    ("clinical_event", r"time to (?:first )?(?:event|exacerbation|relapse|recurrence)|exacerbations?|relapse|hospitali[sz]ation|stroke|myocardial infarction|MACE"),
    ("symptom_score", r"change from baseline|score|scale|questionnaire|\bPRO\b|quality of life|pain intensity|severity"),
    ("biomarker", r"level|concentration|HbA1c|LDL|viral load|reduction in [A-Za-z\- ]+ (?:levels?|counts?)|biomarker|count"),
    ("pk_safety", r"pharmacokinetic|\bAUC\b|\bCmax\b|adverse events?|safety|tolerability|dose[- ]limiting|maximum tolerated"),
]
ENDPOINT_RX = [(k, re.compile(v, re.I)) for k, v in ENDPOINTS]
AREAS = [  # therapeutic area from MeSH ancestor terms
    ("oncology", r"neoplasm|cancer|tumou?r|carcinoma|lymphoma|leukemia|melanoma|sarcoma|myeloma"),
    ("neuro_psych", r"nervous system|mental disorder|behavior|brain|neurodegenerat|epilep|depress|schizophren|alzheimer|parkinson"),
    ("cardio_metabolic", r"cardiovascular|heart|vascular|metabolic|diabetes|obesity|lipid|nutritional"),
    ("infectious", r"infection|bacterial|viral|virus|parasit|fungal|mycoses"),
    ("immunology", r"immune system|autoimmune|arthritis|lupus|psoria|inflammatory bowel|dermatitis|hypersensitivity"),
    ("respiratory", r"respiratory|lung|asthma|pulmonary"),
    ("rare_genetic", r"congenital|hereditary|genetic diseases|inborn"),
]
AREA_RX = [(k, re.compile(v, re.I)) for k, v in AREAS]


def year(s):
    m = re.match(r"(\d{4})", s or "")
    return int(m.group(1)) if m else None


def features(st):
    p = st.get("protocolSection", {})
    d = p.get("designModule", {})
    di = d.get("designInfo", {})
    arms = p.get("armsInterventionsModule", {})
    ag = arms.get("armGroups", []) or []
    iv = arms.get("interventions", []) or []
    outs = (p.get("outcomesModule", {}) or {}).get("primaryOutcomes", []) or []
    sp = (p.get("sponsorCollaboratorsModule", {}) or {})
    cond = p.get("conditionsModule", {}) or {}
    der = (st.get("derivedSection", {}) or {}).get("conditionBrowseModule", {}) or {}
    anc = " ".join(x.get("term", "") for x in (der.get("ancestors") or []) + (der.get("meshes") or [])) + " " + " ".join(cond.get("conditions") or [])
    phases = d.get("phases") or []
    enroll = (d.get("enrollmentInfo") or {}).get("count")
    mask = ((di.get("maskingInfo") or {}).get("masking") or "NONE").upper()
    out_txt = " ".join((o.get("measure") or "") for o in outs)
    endpoint = next((k for k, rx in ENDPOINT_RX if rx.search(out_txt)), "other")
    sy, ey = year((p.get("statusModule", {}).get("startDateStruct") or {}).get("date")), \
        year((p.get("statusModule", {}).get("primaryCompletionDateStruct") or {}).get("date"))
    types = {(x.get("type") or "").upper() for x in iv}
    f = {
        "nct_id": p.get("identificationModule", {}).get("nctId"),
        "phase": "/".join(sorted(phases)),
        "randomized": int((di.get("allocation") or "") == "RANDOMIZED"),
        "masking_level": {"NONE": 0, "SINGLE": 1, "DOUBLE": 2, "TRIPLE": 3, "QUADRUPLE": 4}.get(mask, 0),
        "placebo_arm": int(any((a.get("type") or "") in ("PLACEBO_COMPARATOR", "SHAM_COMPARATOR") for a in ag)),
        "active_comparator": int(any((a.get("type") or "") == "ACTIVE_COMPARATOR" for a in ag)),
        "single_arm": int(len(ag) <= 1),
        "n_arms": len(ag),
        "log_enrollment": math.log1p(enroll) if isinstance(enroll, (int, float)) else None,
        "n_primary_outcomes": len(outs),
        "endpoint": endpoint,
        "biologic": int("BIOLOGICAL" in types),
        "genetic": int("GENETIC" in types),
        "industry_sponsor": int(((sp.get("leadSponsor") or {}).get("class") or "") == "INDUSTRY"),
        "has_collaborator": int(bool(sp.get("collaborators"))),
        "sponsor": (sp.get("leadSponsor") or {}).get("name"),
        "area": next((k for k, rx in AREA_RX if rx.search(anc)), "other"),
        "start_year": sy,
        "duration_years": (ey - sy) if sy and ey else None,
        "conditions": (cond.get("conditions") or [])[:5],
        "interventions": [x.get("name") for x in iv][:5],
        "status": p.get("statusModule", {}).get("overallStatus"),
    }
    return f


def fetch(ids):
    key = CACHE / f"{ids[0]}_{ids[-1]}_{len(ids)}.json"
    if key.exists():
        return json.loads(key.read_text(encoding="utf-8"))
    for i in range(4):
        try:
            r = requests.get(API, params={"filter.ids": ",".join(ids), "pageSize": len(ids), "fields": FIELDS + ",ConditionBrowseModule"}, timeout=60)
            if r.status_code == 200:
                studies = r.json().get("studies", [])
                key.write_text(json.dumps(studies), encoding="utf-8")
                return studies
            time.sleep(2 + 3 * i)
        except requests.RequestException:
            time.sleep(2 + i)
    return []


def main():
    ids = set()
    for f in ("phase1_CTO_rf.csv", "phase2_CTO_rf.csv", "phase3_CTO_rf.csv"):
        ids |= set(pd.read_csv(CTO / f, usecols=["nct_id"])["nct_id"].dropna())
    ids = sorted(ids)
    print(f"{len(ids)} trials to describe", flush=True)
    n = 0
    with open(OUT, "w", encoding="utf-8") as fh:
        for i in range(0, len(ids), 100):
            for st in fetch(ids[i:i + 100]):
                fh.write(json.dumps(features(st)) + "\n"); n += 1
            if (i // 100) % 50 == 0:
                print(f"  {i + 100}/{len(ids)}", flush=True)
            time.sleep(0.25)   # be polite to the public API
    print(f"wrote {n} trials to {OUT}")


if __name__ == "__main__":
    main()
