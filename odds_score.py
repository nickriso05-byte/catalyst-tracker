"""
odds_score.py: model odds for upcoming catalysts (used by build.run during each refresh).

  FDA decision dates  -> odds of approval   (odds/live/fda_model.json)
  Trial readouts      -> odds of success    (odds/live/readout_model.json, calibrated to industry phase rates)

Trials are looked up live on ClinicalTrials.gov (by company + drug), since many ongoing trials are newer than the
training data. When a catalyst can't be tied to a specific trial, the historical average is kept and labeled as such.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from datetime import date
from functools import lru_cache
from pathlib import Path

import requests

import odds_trials as ot
from odds_fda_records import sponsor_key

HERE = Path(__file__).parent
LIVE = HERE / "odds" / "live"
CT_CACHE = HERE / "cache" / "ctgov_live"
CT_CACHE.mkdir(parents=True, exist_ok=True)
ONCO = re.compile(r"cancer|tumou?r|carcinoma|lymphoma|leukemia|myeloma|melanoma|sarcoma|oncolog|metasta|neoplas", re.I)
PH3 = re.compile(r"phase\s*(?:3|iii)\b|pivotal|registrational", re.I)
PH2 = re.compile(r"phase\s*(?:2|ii)(?:[ab])?\b", re.I)
PH1 = re.compile(r"phase\s*(?:1|i)(?:[ab])?\b", re.I)

LABEL = {
    # FDA-decision inputs
    "priority_review": "Priority review", "breakthrough": "Breakthrough therapy", "fast_track": "Fast track",
    "orphan": "Orphan drug", "accelerated": "Accelerated approval pathway", "rtor_or_voucher": "Real-time review / priority voucher",
    "adcom_planned": "Advisory committee planned", "adcom_none": "No advisory committee needed",
    "adcom_positive": "Advisory committee voted in favor", "adcom_negative": "Advisory committee voted against",
    "resubmission": "Resubmission", "resub": "Resubmission after a rejection letter", "supplemental": "New use of an approved drug",
    "bla": "Biologic (BLA)", "nda_505b2": "505(b)(2) application", "extended": "FDA extended the review",
    "manufacturing": "Manufacturing / inspection mentioned", "label_talks": "Label talks / late-cycle meeting", "partnered": "Partnered program",
    "sponsor_has_approval": "Company has an FDA approval", "onc": "Cancer drug",
    "piv_randomized": "Pivotal trial randomized", "piv_placebo": "Pivotal trial placebo-controlled", "piv_active": "Pivotal trial vs. an active drug",
    "piv_single_arm": "Single-arm pivotal trial", "piv_ep_survival": "Survival / event endpoint", "piv_ep_response": "Response-rate endpoint",
    "piv_ep_symptom": "Symptom-score endpoint", "piv_ep_biomarker": "Biomarker endpoint", "piv_phase3": "Phase 3 pivotal trial",
    # readout inputs
    "randomized": "Randomized", "placebo_arm": "Placebo-controlled", "active_comparator": "Head-to-head vs. an active drug",
    "single_arm": "Single-arm", "biologic": "Biologic", "genetic": "Gene / cell therapy", "industry_sponsor": "Industry-run",
    "has_collaborator": "Has a partner", "ep_overall_survival": "Overall-survival endpoint", "ep_progression_free": "Progression-free endpoint",
    "ep_response_rate": "Response-rate endpoint", "ep_clinical_event": "Clinical-event endpoint", "ep_symptom_score": "Symptom-score endpoint",
    "ep_biomarker": "Biomarker endpoint", "ep_pk_safety": "Safety / dosing endpoint", "ar_oncology": "Cancer", "ar_neuro_psych": "Neurology / psychiatry",
    "ar_cardio_metabolic": "Cardiometabolic", "ar_infectious": "Infectious disease", "ar_immunology": "Immunology", "ar_respiratory": "Respiratory",
    "ar_rare_genetic": "Rare / genetic disease",
}


@lru_cache(maxsize=None)
def _j(name):
    p = LIVE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


_SUFFIX = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|lp|ag|sa|nv|n\.v|s\.a|se|ab|asa|"
                     r"holdings?|group|the)\b\.?", re.I)


def _search_name(company: str) -> str:
    """'enGene Holdings Inc.' -> 'enGene'; 'Capricor Therapeutics, Inc.' -> 'Capricor Therapeutics'."""
    s = _SUFFIX.sub(" ", re.sub(r"[,/]|\s+-\s+.*$", " ", company or ""))
    return " ".join(s.split()[:2])


def _ct_search(company: str, drug: str | None):
    """Industry trials for a company (and drug) on ClinicalTrials.gov, cached for a week."""
    params = {"query.spons": _search_name(company), "pageSize": 50, "fields": ot.FIELDS + ",ConditionBrowseModule"}
    if drug:
        params["query.intr"] = drug
    key = CT_CACHE / (hashlib.sha1(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")
    if key.exists() and time.time() - key.stat().st_mtime < 7 * 86400:
        studies = json.loads(key.read_text(encoding="utf-8"))
    else:
        studies = []
        for i in range(3):
            try:
                r = requests.get(ot.API, params=params, timeout=40)
                if r.status_code == 200:
                    studies = r.json().get("studies", [])
                    key.write_text(json.dumps(studies), encoding="utf-8")
                    break
            except requests.RequestException:
                pass
            time.sleep(1 + i)
    out = []
    for st in studies:
        f = ot.features(st)
        f["enrollment"] = ((st.get("protocolSection", {}).get("designModule", {}) or {}).get("enrollmentInfo") or {}).get("count")
        out.append(f)
    return out


def _phase_family(ph):
    return {"PHASE3": 3, "PHASE2/PHASE3": 3, "PHASE2": 2, "PHASE1/PHASE2": 1.5, "PHASE1": 1}.get(ph)


def _drug_hit(t, drug):
    d = (drug or "").lower()
    names = " ".join(t.get("interventions") or []).lower()
    return bool(d) and len(d) >= 4 and (d in names or any(len(n) >= 4 and n in d for n in re.split(r"[^a-z0-9-]+", names)))


# ------------------------------------------------------------------ trial readouts

def readout_odds(e: dict, company: str):
    m, hist = _j("readout_model.json"), _j("readout_history.json")
    if not m or not hist:
        return None
    q = e.get("quote") or ""
    explicit = re.search(r"phase\s*(?:3|iii)\b", q, re.I) and 3 or re.search(r"phase\s*(?:2|ii)(?:[ab])?\b", q, re.I) and 2 or \
        re.search(r"phase\s*(?:1|i)(?:[ab])?\b", q, re.I) and 1 or None
    pivotal = bool(re.search(r"pivotal|registrational", q, re.I))
    year = date.today().year

    def ok(t):
        fam = _phase_family(t.get("phase"))
        if fam is None or (t.get("status") or "").upper() in ("WITHDRAWN", "TERMINATED"):
            return False
        end = (t.get("start_year") or 0) + (t.get("duration_years") or 2)
        if not t.get("start_year") or t["start_year"] > year or end < year - 1:
            return False                               # not ongoing / not just finished
        if explicit and abs(fam - explicit) > 0.5:
            return False
        if not explicit and pivotal and fam < 2:
            return False                               # "pivotal" can be a Phase 2 cohort, never Phase 1
        return True

    cands = [t for t in _ct_search(company, e.get("drug")) if ok(t)]
    if e.get("drug"):
        hit = [t for t in cands if _drug_hit(t, e["drug"])]
        if not hit:   # drug named in the filing may be a trial code; try the company's trials without the drug filter
            hit = [t for t in _ct_search(company, None) if ok(t) and _drug_hit(t, e["drug"])]
        cands = hit
    # Phase 2+ trials whose main goal is safety / dosing are usually open-label extensions, not the readout
    cands = [t for t in cands if not ((_phase_family(t["phase"]) or 0) >= 2 and t.get("endpoint") == "pk_safety")]
    if not cands:
        return None                                    # can't tie it to a trial: keep the historical average
    drugs = {(t.get("interventions") or [""])[0].lower() for t in cands}
    if not e.get("drug") and len(drugs) > 1:
        return None                                    # several programs could match: don't guess
    t = max(cands, key=lambda t: (_phase_family(t["phase"]) or 0, t.get("randomized") or 0, t.get("start_year") or 0, t.get("log_enrollment") or 0))
    import odds_readout as ro
    r = {"phase": t["phase"], "area": t.get("area") or "other", "endpoint": t.get("endpoint") or "other",
         "sponsor": (t.get("sponsor") or "").lower() or None, "drug": ro.drug_key(t.get("interventions"))}
    for f in ("randomized", "masking_level", "placebo_arm", "active_comparator", "single_arm", "biologic", "genetic", "industry_sponsor", "has_collaborator"):
        r[f] = float(t.get(f) or 0)
    r["n_arms_c"] = float(min(t.get("n_arms") or 0, 6)); r["n_primary_c"] = float(min(t.get("n_primary_outcomes") or 0, 5))
    for p in ro.PHASES:
        r[f"ph_{p}"] = float(r["phase"] == p)
    for x in ro.ENDPOINTS:
        r[f"ep_{x}"] = float(r["endpoint"] == x)
    for a in ro.AREAS:
        r[f"ar_{a}"] = float(r["area"] == a)
    r["start_c"] = float(min(max(t["start_year"], 1995), 2026) - 2010) / 10
    g = (hist["global"][0] + 1) / (hist["global"][1] + 2)

    def rate(tab, key, mm):
        a = hist[tab].get(key) if key else None
        a = a or [0, 0]
        return (a[0] + mm * g) / (a[1] + mm), a[1], a[0]
    r["prec_phase_area"] = rate("pa", f"{r['phase']}|{r['area']}", 10)[0]
    r["prec_phase_endpoint"] = rate("pe", f"{r['phase']}|{r['endpoint']}", 10)[0]
    sr, sn, _ = rate("sp", r["sponsor"], 5)
    r["sponsor_rate"], r["sponsor_log_n"], r["sponsor_first"] = sr, math.log1p(sn), float(sn == 0)
    dr, dn, ds = rate("dr", r["drug"], 3)
    r["drug_rate"], r["drug_log_n"], r["drug_prior_success"] = dr, math.log1p(dn), float(ds > 0)
    z = sum(c * r[f] for f, c in zip(m["features"], m["coef"])) + m["intercept"] + (m.get("phase_offsets") or {}).get(t["phase"], 0.0)
    p = 1 / (1 + math.exp(-z))
    coef = dict(zip(m["features"], m["coef"]))
    drivers = []
    for f in ("randomized", "placebo_arm", "active_comparator", "single_arm", "biologic", "genetic", "has_collaborator",
              f"ep_{r['endpoint']}", f"ar_{r['area']}"):
        if r.get(f) and f in LABEL and abs(coef.get(f, 0)) > 0.02:
            drivers.append([LABEL[f], round(coef[f], 2)])
    if sn:
        drivers.append([f"Sponsor's past trials: {round(100 * sr)}% succeeded (n={sn})", round(coef["sponsor_rate"] * (sr - g), 2)])
    else:
        drivers.append(["First trial on record for this sponsor", round(coef.get("sponsor_first", 0), 2)])
    if dn:
        drivers.append([f"This drug's earlier trials: {ds} of {dn} succeeded", round(coef["drug_rate"] * (dr - g) + coef.get("drug_prior_success", 0) * float(ds > 0), 2)])
    drivers.sort(key=lambda d: -abs(d[1]))
    return {"p": round(p, 3), "kind": "readout", "nct": t.get("nct_id"), "phase": t["phase"].replace("PHASE", "Phase ").replace("/", " / "),
            "drivers": drivers[:6], "basis": "trial-readout model, calibrated to industry phase rates"}


# ------------------------------------------------------------------ FDA decisions

def fda_odds(e: dict, company: str, cik: int | None):
    m, spa = _j("fda_model.json"), _j("sponsor_approvals.json")
    if not m:
        return None
    import odds_live as ol
    feats, n = (ol.features(cik, e["start"], e.get("drug")) if cik else ({}, 0))
    feats = {k: float(v) for k, v in (feats or {}).items()}
    k = sponsor_key(company)
    today = date.today().isoformat()
    na = sum(1 for d in (spa or {}).get(k or "", []) if d < today)
    x = {f: feats.get(f, 0.0) for f in m["features"]}
    x["resub"] = float(feats.get("resubmission", 0) or feats.get("prior_crl", 0))
    x["sponsor_has_approval"], x["sponsor_log_approvals"] = float(na > 0), math.log1p(na)
    x["onc"] = float(bool(ONCO.search(" ".join(filter(None, [e.get("quote"), e.get("drug"), e.get("indication")])))))
    # pivotal trial: the company's late-stage trial of this drug that has finished
    year = int(e["start"][:4])
    piv = None
    if e.get("drug"):
        c = [t for t in _ct_search(company, e["drug"]) if _phase_family(t.get("phase")) in (3, 2)
             and t.get("start_year") and t["start_year"] + (t.get("duration_years") or 2) <= year and _drug_hit(t, e["drug"])]
        if c:
            piv = max(c, key=lambda t: (_phase_family(t["phase"]), t["start_year"]))
    if piv:
        ep = piv.get("endpoint")
        x.update({"piv_linked": 1.0, "piv_randomized": float(piv.get("randomized") or 0), "piv_placebo": float(piv.get("placebo_arm") or 0),
                  "piv_active": float(piv.get("active_comparator") or 0), "piv_masking": float(piv.get("masking_level") or 0),
                  "piv_single_arm": float(piv.get("single_arm") or 0), "piv_log_n": float(piv.get("log_enrollment") or 0),
                  "piv_ep_survival": float(ep in ("overall_survival", "progression_free", "clinical_event")),
                  "piv_ep_response": float(ep == "response_rate"), "piv_ep_symptom": float(ep == "symptom_score"),
                  "piv_ep_biomarker": float(ep == "biomarker"), "piv_phase3": float(piv.get("phase") in ("PHASE3", "PHASE2/PHASE3"))})
        if piv.get("area") == "oncology":
            x["onc"] = 1.0
    l1 = m["layer1"]
    l1x = {"bla": x.get("bla", 0), "supplemental": x.get("supplemental", 0), "resubmission": x["resub"], "onc": x["onc"],
           "sponsor_has_approval": x["sponsor_has_approval"], "sponsor_log_approvals": x["sponsor_log_approvals"]}
    x["l1_logit"] = sum(c * l1x[f] for f, c in zip(l1["features"], l1["coef"])) + l1["intercept"]
    z = sum(c * x.get(f, 0.0) for f, c in zip(m["features"], m["coef"])) + m["intercept"]
    p = 1 / (1 + math.exp(-z))
    coef = dict(zip(m["features"], m["coef"]))
    drivers = []
    for f, v in x.items():
        if f in ("l1_logit", "sponsor_log_approvals", "piv_linked", "piv_masking", "piv_log_n", "sponsor_has_approval") or not v:
            continue
        if f in LABEL and abs(coef.get(f, 0)) > 0.02:
            drivers.append([LABEL[f], round(coef[f], 2)])
    drivers.append([f"Company's FDA approvals to date: {na}" if na else "No prior FDA approval for this company",
                    round(coef.get("sponsor_has_approval", 0) * float(na > 0) + coef.get("sponsor_log_approvals", 0) * math.log1p(na), 2) or -0.05])
    drivers.sort(key=lambda d: -abs(d[1]))
    return {"p": round(p, 3), "kind": "fda", "nct": piv.get("nct_id") if piv else None, "filings": n,
            "drivers": drivers[:6], "basis": "FDA-decision model (FDA records + company filings + pivotal trial)"}


def score_event(e: dict, company: str, cik: int | None):
    try:
        if e["type"] in ("PDUFA date", "Advisory committee") and e.get("precision") == "day":
            return fda_odds(e, company, cik)
        if e["type"] == "Data readout":
            return readout_odds(e, company)
    except Exception:
        return None
    return None
