"""
odds_fda_records.py: FDA decisions straight from the FDA (openFDA), to grow the decision history.

  Approvals:  Drugs@FDA, original NDA/BLA approvals and new-use (efficacy) supplement approvals
  Rejections: the FDA's published Complete Response Letters (CRL database, mostly 2016 onward)
Window: 2016 onward for both, so approvals and rejections cover the same years.

Each decision gets only facts known before it:
  review priority, new molecular entity or not, biologic or not, original vs. supplement,
  resubmission after an earlier CRL, sponsor's prior approvals, cancer drug or not.
Output: odds/fda_decisions.csv
"""
from __future__ import annotations

import csv
import json
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "cache" / "openfda"
CACHE.mkdir(parents=True, exist_ok=True)
START = "2016-01-01"
ONCO = re.compile(r"kinase inhibitor|antineoplastic|alkylating|antimetabolite|programmed death|cd20|cd38|bcma|her2|egfr|alk |"
                  r"antibody-drug conjugate|radiopharmaceutical|parp|bcl-2|cdk4|topoisomerase|vinca|tubulin|proteasome|cancer|tumor|oncolog|lymphoma|leukemia|myeloma", re.I)


def get(url, key):
    p = CACHE / (re.sub(r"[^A-Za-z0-9]+", "_", key)[:150] + ".json")
    if p.exists() and time.time() - p.stat().st_mtime < 7 * 86400:
        return json.loads(p.read_text(encoding="utf-8"))
    import urllib.error
    for i in range(5):
        try:
            d = json.load(urllib.request.urlopen(url, timeout=90))
            p.write_text(json.dumps(d), encoding="utf-8")
            return d
        except urllib.error.HTTPError as e:
            if e.code == 404:   # openFDA's "no matches": cache it as empty instead of retrying
                d = {"meta": {"results": {"total": 0}}, "results": []}
                p.write_text(json.dumps(d), encoding="utf-8")
                return d
            time.sleep(2 + 3 * i)
        except Exception:
            time.sleep(2 + 3 * i)
    return None


def all_apps(prefix):
    """Every Drugs@FDA application whose number starts with prefix (NDA / BLA). Paged by sponsor letter to stay under openFDA's skip limit."""
    out = {}
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        skip = 0
        while True:
            q = f"application_number:{prefix}*+AND+sponsor_name:{letter}*"
            d = get(f"https://api.fda.gov/drug/drugsfda.json?search={q}&limit=1000&skip={skip}", f"{prefix}_{letter}_{skip}")
            if not d or "results" not in d:
                break
            for r in d["results"]:
                out[r["application_number"]] = r
            skip += 1000
            if skip >= d["meta"]["results"]["total"] or skip > 24000:
                break
    return out


def crl_letters():
    rows, skip = [], 0
    while True:
        d = get(f"https://api.fda.gov/transparency/crl.json?limit=100&skip={skip}", f"crl_{skip}")
        if not d:
            break
        rows += d["results"]
        skip += 100
        if skip >= d["meta"]["results"]["total"]:
            break
    return rows


def ymd(s):
    s = (s or "").strip()
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s)
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else None


def norm_app(a):
    m = re.search(r"(NDA|BLA|BL)\s*0*(\d{5,6})", str(a or ""), re.I)
    if not m:
        return None
    kind = "BLA" if m.group(1).upper() in ("BLA", "BL") else "NDA"
    return f"{kind}{int(m.group(2)):06d}"


GENERIC = set("""inc llc ltd limited corp corporation co company plc ag sa nv bv gmbh kk holdings holding group
pharmaceuticals pharmaceutical pharma pharms pharm therapeutics therapeutic biosciences bioscience biotherapeutics bio biologics
biopharma biopharmaceuticals sciences science us usa america americas north international intl global worldwide dmcc labs
laboratories lab research medical healthcare health the and of de c o attn attention""".split())


def sponsor_key(s):
    """One key per company across FDA records, CRL letters and SEC filings: its first distinctive word
    ('Zydus Worldwide DMCC' and 'ZYDUS PHARMS' -> 'zydus'; 'Capricor Therapeutics, Inc.' -> 'capricor')."""
    words = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
    for w in words:
        if w not in GENERIC and len(w) >= 3 and not w.isdigit():
            return w
    return None


norm_sponsor = sponsor_key


def main():
    apps = {}
    for prefix in ("NDA", "BLA"):
        got = all_apps(prefix)
        print(f"{prefix}: {len(got)} applications", flush=True)
        apps.update({norm_app(k): v for k, v in got.items() if norm_app(k)})

    decisions = []
    orig_info = {}
    for app, r in apps.items():
        epc = " ".join((r.get("openfda") or {}).get("pharm_class_epc", []) + (r.get("openfda") or {}).get("pharm_class_moa", []))
        names = " ".join((r.get("openfda") or {}).get("generic_name", []) + [p.get("brand_name", "") for p in r.get("products", [])])
        onc = int(bool(ONCO.search(epc + " " + names)))
        for s in r.get("submissions", []):
            st, ty, cls = s.get("submission_status"), s.get("submission_type"), (s.get("submission_class_code") or "").upper()
            d = ymd(s.get("submission_status_date"))
            if ty == "ORIG":
                orig_info[app] = {"priority": int((s.get("review_priority") or "").upper().startswith("PRIORITY")),
                                  "nme": int(cls == "TYPE 1"), "class": cls, "onc": onc, "sponsor": r.get("sponsor_name")}
            if st != "AP" or not d or d < START:
                continue
            if ty == "ORIG" or (ty == "SUPPL" and cls == "EFFICACY"):
                decisions.append({"app": app, "date": d, "y": 1, "supplemental": int(ty == "SUPPL"),
                                  "priority": int((s.get("review_priority") or "").upper().startswith("PRIORITY")),
                                  "nme": int(cls == "TYPE 1") if ty == "ORIG" else 0, "onc": onc,
                                  "bla": int(app.startswith("BLA")), "sponsor": r.get("sponsor_name"), "source": "Drugs@FDA"})

    crls = crl_letters()
    print(f"CRL letters: {len(crls)}", flush=True)
    crl_dates = defaultdict(list)
    for c in crls:
        if (c.get("letter_type") or "").upper() != "COMPLETE RESPONSE":
            continue
        d = ymd(c.get("letter_date"))
        app = norm_app(c.get("application_number"))
        if not d or not app:
            continue
        crl_dates[app].append(d)
        if d < START:
            continue
        info = orig_info.get(app, {})
        supp = int(not re.search(r"Orig1s000", c.get("file_name") or "", re.I))
        onc = info.get("onc")
        if onc is None:   # never-approved drug: read the indication from the letter itself
            onc = int(bool(ONCO.search(c.get("text") or "")))
        decisions.append({"app": app, "date": d, "y": 0, "supplemental": supp, "priority": info.get("priority"),
                          "nme": info.get("nme") if not supp else 0, "onc": onc, "bla": int(app.startswith("BLA")),
                          "sponsor": info.get("sponsor") or c.get("company_name"), "source": "FDA CRL letter"})

    # facts known before each decision
    sponsor_approvals = defaultdict(list)
    for app, info in orig_info.items():
        r = apps.get(app, {})
        for s in r.get("submissions", []):
            if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP":
                d = ymd(s.get("submission_status_date"))
                if d:
                    sponsor_approvals[norm_sponsor(r.get("sponsor_name"))].append(d)
    for x in decisions:
        x["resubmission"] = int(any(c < x["date"] for c in crl_dates.get(x["app"], [])))
        sp = norm_sponsor(x["sponsor"])
        x["sponsor_prior_approvals"] = sum(1 for d in sponsor_approvals.get(sp, []) if d < x["date"])
        x["priority_known"] = int(x["priority"] is not None)
        x["priority"] = x["priority"] or 0
        x["nme_known"] = int(x["nme"] is not None)
        x["nme"] = x["nme"] or 0
        x["onc_known"] = int(x["onc"] is not None)
        x["onc"] = x["onc"] or 0

    (HERE / "odds" / "sponsor_approvals.json").write_text(json.dumps({k: sorted(v) for k, v in sponsor_approvals.items() if k}), encoding="utf-8")
    cols = ["app", "date", "y", "source", "sponsor", "supplemental", "priority", "priority_known", "nme", "nme_known", "bla", "onc", "onc_known",
            "resubmission", "sponsor_prior_approvals"]
    with open(HERE / "odds" / "fda_decisions.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for x in sorted(decisions, key=lambda x: x["date"]):
            w.writerow(x)
    n1 = sum(x["y"] for x in decisions)
    print(f"decisions since {START}: {len(decisions)} ({n1} approvals, {len(decisions) - n1} CRLs)")


if __name__ == "__main__":
    main()
