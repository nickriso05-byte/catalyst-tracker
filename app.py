"""
FDA Catalyst Tracker: Streamlit app.

The interface is the hand-built HTML page in ui/ (calendar, screens, company card), mounted as a
Streamlit v2 component. Python does three things behind it:
- serves the snapshot in public/data.json, which a background thread rebuilds on a schedule;
- builds a live company card when someone opens one (the "open" trigger from the page);
- keeps the password in front of everything (gate.py).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

import build
import gate

HERE = Path(__file__).parent

st.set_page_config(page_title="FDA Catalyst Tracker", page_icon="💊", layout="wide", initial_sidebar_state="collapsed")
# Let the page own the whole viewport: no Streamlit header, toolbar or padding.
st.html("""<style>
@import url('https://fonts.googleapis.com/css2?family=Figtree:wght@400;500;700&family=Libre+Caslon+Text:wght@400;700&display=swap');
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] { background: #fefefe; }
[data-testid="stHeader"], #MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none !important; }
.block-container, [data-testid="stMainBlockContainer"] { padding: 0 !important; max-width: none !important; }
[data-testid="stVerticalBlock"] { gap: 0 !important; }
</style>""")

# --------------------------------------------------------------------------- scheduled refresh

REFRESH_MARKET_MIN = 30      # Mon–Fri, 6 AM to 8 PM Eastern
REFRESH_OFF_HOURS_MIN = 180  # nights and weekends
RETRY_AFTER_FAIL_MIN = 20


def _et_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:  # no tz database: assume EST
        return datetime.now(timezone(timedelta(hours=-5)))


def _mtime():
    try:
        return build.OUT.stat().st_mtime
    except FileNotFoundError:
        return None


def refresh_interval_min():
    n = _et_now()
    return REFRESH_MARKET_MIN if n.weekday() < 5 and 6 <= n.hour < 20 else REFRESH_OFF_HOURS_MIN


@st.cache_resource
def refresher():
    """One background refresher per server process, shared by every visitor."""
    state = {"running": False, "msg": "", "last_err": None, "failed_at": 0.0, "lock": threading.Lock()}

    def due():
        m = _mtime()
        if m is None:
            return True
        if time.time() - state["failed_at"] < RETRY_AFTER_FAIL_MIN * 60:
            return False
        return (time.time() - m) / 60 >= refresh_interval_min()

    def once():
        if not state["lock"].acquire(blocking=False):
            return
        state.update(running=True, msg="Starting refresh")
        try:
            build.run(log=lambda m: (state.update(msg=m), print(f"[refresh] {m}", flush=True)))
            state["last_err"] = None
        except Exception as ex:  # keep serving the last good snapshot
            state.update(last_err=f"{type(ex).__name__}: {ex}", failed_at=time.time())
            print(f"[refresh] FAILED: {type(ex).__name__}: {ex}", flush=True)
        finally:
            state["running"] = False
            state["lock"].release()

    def loop():
        while True:
            try:
                if due():
                    once()
            except Exception:
                pass
            time.sleep(60)

    state["run_now"] = lambda: threading.Thread(target=once, daemon=True).start()
    threading.Thread(target=loop, daemon=True, name="catalyst-refresher").start()
    return state


R = refresher()     # starts the background refresh even while the login screen is up
gate.require()

# --------------------------------------------------------------------------- data


@st.cache_data(show_spinner=False)
def load_snapshot(mtime: float):
    return json.loads(build.OUT.read_text(encoding="utf-8"))


@st.cache_data(ttl=300, show_spinner=False)
def live(tk: str, events_json: str, since: str | None):
    """Live card for one ticker, re-used for 5 minutes."""
    c, new = build.live_card(tk, json.loads(events_json), since)
    return c, new, time.time()


m = _mtime()
D = load_snapshot(m) if m else None

# --------------------------------------------------------------------------- Verdad-style scan (colleague's daily CSV)
# Read from VERDAD_CSV_URL (e.g. a Google Sheet published as CSV) if set, else verdad/latest.csv.
# Neither is in the public repo.
VERDAD_LOCAL = HERE / "verdad" / "latest.csv"


def _secret(name):
    import os
    if os.environ.get(name):
        return os.environ[name]
    try:
        return st.secrets.get(name)
    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def load_verdad(url: str | None, local_mtime: float | None):
    import csv
    import io
    if url:
        import requests
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        text, asof = r.content.decode("utf-8-sig"), r.headers.get("Last-Modified")
        asof = datetime.now(timezone.utc).isoformat() if not asof else asof
    elif local_mtime:
        text, asof = VERDAD_LOCAL.read_text(encoding="utf-8-sig"), datetime.fromtimestamp(local_mtime, timezone.utc).isoformat()
    else:
        return None

    def f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    rows = {}
    for r in csv.DictReader(io.StringIO(text)):
        tk = (r.get("ticker") or "").strip().upper()
        if not tk or f(r.get("score")) is None:
            continue
        rows[tk] = {"score": f(r["score"]), "tier1": f(r.get("tier1")), "tier2": f(r.get("tier2")), "funds": (r.get("tier1_funds") or "").strip(),
                    "short_float": f(r.get("short_float")), "short": r.get("short_signal") or None,
                    "insider_trans": f(r.get("insider_trans")), "insider": r.get("insider_signal") or None,
                    "peer_mom": f(r.get("peer_mom")), "peer": r.get("peer_signal") or None,
                    "ev_to_burn": f(r.get("ev_to_burn")), "value": r.get("value_signal") or None,
                    "current_ratio": f(r.get("current_ratio")), "cash": r.get("cash_signal") or None}
    scores = sorted(v["score"] for v in rows.values())
    n = len(scores)
    import bisect
    for v in rows.values():
        v["rank"] = 1 + n - bisect.bisect_right(scores, v["score"])         # 1 = best; ties share a rank
        v["pctile"] = round(100 * (n - v["rank"]) / max(n - 1, 1))           # share of the scan ranked below it
    return {"asof": asof, "n": n, "rows": rows}


try:
    V = load_verdad(_secret("VERDAD_CSV_URL"), VERDAD_LOCAL.stat().st_mtime if VERDAD_LOCAL.exists() else None)
except Exception:
    V = None

# --------------------------------------------------------------------------- page <-> Python

ss = st.session_state
if "card_tk" not in ss:
    q = st.query_params.get("t")
    ss["card_tk"] = q.upper() if q else None


def _result(name):
    res = ss.get("ct")
    if res is None:
        return None
    try:
        return res[name]
    except Exception:
        return getattr(res, name, None)


def _on_open():
    v = _result("open")
    if v:
        ss["card_tk"] = str(v).upper()


def _on_close():
    ss["card_tk"] = None


def _on_refresh():
    if not R["running"]:
        R["run_now"]()


card = None
tk = ss.get("card_tk")
if tk:
    known = [e for e in (D or {}).get("events", []) if e["ticker"] == tk]
    since = D["generated_at"][:10] if D else (date.today() - timedelta(days=150)).isoformat()
    try:
        t, new, fetched = live(tk, json.dumps(known), since)
        if t and D and D.get("score_dist"):
            t = {**t, "vscore": build.score_from(t.get("factors") or {}, D["score_dist"])}   # scored live, vs. this refresh
        card = {"tk": tk, "t": t, "new": new, "fetched": fetched}
    except Exception as ex:
        card = {"tk": tk, "t": None, "new": [], "fetched": time.time(),
                "error": f"Couldn't load {tk} right now ({type(ex).__name__}). Try again in a minute."}

@st.cache_resource
def ui_component(stamp: float):
    """Registered once per version of the ui/ files (re-registering on every rerun logs a warning)."""
    return st.components.v2.component(
        "catalyst_ui",
        html=(HERE / "ui" / "ui.html").read_text(encoding="utf-8"),
        css=(HERE / "ui" / "ui.css").read_text(encoding="utf-8"),
        js=(HERE / "ui" / "ui.js").read_text(encoding="utf-8"),
    )


UI = ui_component(max(p.stat().st_mtime for p in (HERE / "ui").glob("ui.*")))
UI(key="ct", data={"snap": D, "card": card, "cardTk": tk, "refreshing": R["running"], "verdad": V},
   on_open_change=_on_open, on_close_change=_on_close, on_refresh_change=_on_refresh)


# While the calendar is still building (fresh server) or refreshing, check every 5 seconds and redraw when new data lands.
@st.fragment(run_every=5)
def _watch(seen):
    if _mtime() != seen:
        st.rerun(scope="app")


if not D or D.get("phase") == "calendar" or R["running"]:
    _watch(m)
