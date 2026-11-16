"""
FDA Catalyst Tracker: Streamlit app.

- Calendar and Screens read the snapshot in public/data.json.
- A background thread in the server rebuilds that snapshot on a schedule (refresher below).
- Opening a company card re-pulls that one company live: price, insider trades, 13D/13G,
  share-sale filings, options, and any filings made since the snapshot (checked for new catalysts).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime, timezone, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import build
import card
import gate

st.set_page_config(page_title="FDA Catalyst Tracker", page_icon="💊", layout="wide")
st.html(card.CSS + """<style>
.block-container{padding-top:3rem;max-width:1280px}
[data-testid="stHeader"]{background:transparent;height:0}
div[data-testid="stDialog"] div[role="dialog"]{width:min(1000px,96vw)}
</style>""")
gate.require()

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


def _snapshot_age_min():
    try:
        return (time.time() - build.OUT.stat().st_mtime) / 60
    except FileNotFoundError:
        return None


def refresh_interval_min():
    n = _et_now()
    return REFRESH_MARKET_MIN if n.weekday() < 5 and 6 <= n.hour < 20 else REFRESH_OFF_HOURS_MIN


@st.cache_resource
def refresher():
    """One background refresher per server process (shared by every visitor)."""
    state = {"running": False, "msg": "", "last_ok": None, "last_err": None, "failed_at": 0.0, "lock": threading.Lock()}

    def due():
        age = _snapshot_age_min()
        if age is None:
            return True
        if time.time() - state["failed_at"] < RETRY_AFTER_FAIL_MIN * 60:
            return False
        return age >= refresh_interval_min()

    def once():
        if not state["lock"].acquire(blocking=False):
            return
        state.update(running=True, msg="Starting refresh")
        try:
            build.run(log=lambda m: state.update(msg=m))
            state.update(last_ok=time.time(), last_err=None)
        except Exception as ex:  # keep serving the old snapshot
            state.update(last_err=f"{type(ex).__name__}: {ex}", failed_at=time.time())
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


R = refresher()

# --------------------------------------------------------------------------- data


@st.cache_data(show_spinner=False)
def load_snapshot(mtime: float):
    D = json.loads(build.OUT.read_text(encoding="utf-8"))
    PR = {"day": 0, "month": 1, "quarter": 2, "half": 3, "year": 4}
    D["events"].sort(key=lambda e: (e["end"], PR[e["precision"]], e["label"], e["start"], e["ticker"]))
    if not D.get("universe"):
        D["universe"] = {tk: v.get("name") for tk, v in D["tickers"].items()}
    return D


@st.cache_data(ttl=300, show_spinner=False)
def live(tk: str, events_json: str, since: str | None):
    """Live card for one ticker, re-used for 5 minutes."""
    c, new = build.live_card(tk, json.loads(events_json), since)
    return c, new, time.time()


def snapshot():
    try:
        return load_snapshot(build.OUT.stat().st_mtime)
    except FileNotFoundError:
        return None


D = snapshot()
today = date.today()

# --------------------------------------------------------------------------- header

age = _snapshot_age_min()
if D:
    gen = datetime.fromisoformat(D["generated_at"]).astimezone(_et_now().tzinfo)
    stamp = f"Calendar updated {gen:%b} {gen.day}, {gen.hour % 12 or 12}:{gen:%M %p} ET"
else:
    stamp = "Building the calendar…"
if R["running"]:
    stamp += f" · refreshing now ({card.esc(R['msg'])})"
st.html(f'<div class="ct-top"><div class="b">CATALYST TRACKER <span>· FDA &amp; Biotech</span></div><div class="s">{stamp}</div></div>'
        '<div class="ct-sub"><span>Ray Blanco\'s Catalyst Trader</span><span class="pub">FDA Catalyst Tracker</span></div>')

if not D:
    st.info("The first build of the catalyst calendar is running. It takes about 15 to 20 minutes on a fresh server. "
            "Company lookups below already work, live." + (f" Progress: {R['msg']}" if R["msg"] else ""))

# --------------------------------------------------------------------------- company card (dialog)


def ev_for(tk):
    return [e for e in (D["events"] if D else []) if e["ticker"] == tk]


def price_chart(vals):
    fig = go.Figure(go.Scatter(y=vals, mode="lines", line=dict(color="#003e51", width=2),
                               hovertemplate="$%{y:.2f}<extra></extra>"))
    fig.update_layout(height=130, margin=dict(l=0, r=0, t=4, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(visible=False), yaxis=dict(showgrid=True, gridcolor="#eee", tickprefix="$", tickfont=dict(size=11)),
                      hovermode="x unified", showlegend=False)
    return fig


def short_chart(hist):
    hist = [h for h in hist if h[1] is not None]
    fig = go.Figure(go.Scatter(x=[h[0] for h in hist], y=[h[1] * 100 for h in hist], mode="lines+markers",
                               line=dict(color="#003e51", width=2), marker=dict(size=8, color="#003e51", line=dict(color="#fff", width=2)),
                               hovertemplate="%{x|%b %d, %Y}: %{y:.1f}% of float<extra></extra>"))
    fig.update_layout(height=150, margin=dict(l=0, r=0, t=4, b=0), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      xaxis=dict(showgrid=False, tickfont=dict(size=11)), yaxis=dict(showgrid=True, gridcolor="#eee", ticksuffix="%", tickfont=dict(size=11)),
                      showlegend=False)
    return fig


@st.dialog("Company card", width="large")
def show_card(tk: str):
    known = ev_for(tk)
    since = (D["generated_at"][:10] if D else (today - timedelta(days=150)).isoformat())
    with st.spinner(f"Pulling live data for {tk}…"):
        try:
            t, new, fetched = live(tk, json.dumps(known), since)
        except Exception as ex:
            st.error(f"Couldn't load {tk} right now ({type(ex).__name__}). Try again in a minute.")
            return
    if not t:
        st.warning(f"{tk} isn't in the Healthcare sector, or Unusual Whales has no data for it.")
        return
    evs = sorted(known + new, key=lambda e: e["end"])
    st.html(card.head(tk, t))
    if t.get("spark"):
        st.markdown("**PRICE, LAST 3 MONTHS**")
        st.plotly_chart(price_chart(t["spark"]), use_container_width=True, config={"displayModeBar": False})
    st.html(card.body(tk, t, evs, today, ""))
    if (t.get("short") or {}).get("history") and len(t["short"]["history"]) > 2:
        st.plotly_chart(short_chart(t["short"]["history"]), use_container_width=True, config={"displayModeBar": False})
    st.html(card.tables(t))
    ft = datetime.fromtimestamp(fetched, _et_now().tzinfo)
    st.html(card.FOOT.format(asof=f"Live as of {ft.hour % 12 or 12}:{ft:%M %p} ET (re-pulled at most every 5 minutes)."))


def open_card(tk):
    st.session_state["open_card"] = tk


# --------------------------------------------------------------------------- search any ticker

uni = (D or {}).get("universe") or {}
if not uni:
    try:
        uni = {tk: (nm or "").title() for tk, nm in build.healthcare_universe(25e6).items()}
    except Exception:
        uni = {}
opts = [f"{tk} · {nm}" for tk, nm in sorted(uni.items())]


def _picked():
    v = st.session_state.get("lookup")
    if v:
        open_card(v.split(" · ")[0])
        st.session_state["lookup"] = None


c1, c2 = st.columns([3, 1])
with c1:
    st.selectbox("Look up any healthcare company", opts, index=None, key="lookup", on_change=_picked,
                 placeholder=f"Search {len(opts)} healthcare stocks by ticker or name…", accept_new_options=True)
with c2:
    st.write("")
    if st.button("Refresh calendar now", disabled=R["running"], use_container_width=True,
                 help="Re-runs the full SEC + Unusual Whales refresh in the background (about 5 to 15 minutes)."):
        R["run_now"]()
        st.toast("Refresh started. The calendar updates when it finishes.")

# --------------------------------------------------------------------------- calendar + screens

TYPES = ["PDUFA date", "Advisory committee", "Data readout", "Regulatory submission", "EU decision"]


def signals(tk, e=None):
    t = (D["tickers"].get(tk) or {}) if D else {}
    out = []
    if (t.get("insiders") or {}).get("buy_90d"):
        out.append("Insider buy")
    if t.get("stakes"):
        out.append("New " + t["stakes"][0]["form"])
    if t.get("dilution"):
        out.append("Offering")
    if ((t.get("short") or {}).get("si_float") or 0) >= 0.2:
        out.append("High short")
    if e and e.get("was"):
        out.append(f"Moved {e.get('moved')}")
    return " · ".join(out)


def runway_months(tk):
    r = (D["tickers"].get(tk) or {}).get("runway") or {}
    return r.get("months")


def cash_before(tk, e):
    r = (D["tickers"].get(tk) or {}).get("runway") or {}
    return bool(r.get("runs_out") and r["runs_out"] < e["end"])


def implied_for(tk, e):
    im = (D["tickers"].get(tk) or {}).get("implied")
    return im["move_pct"] * 100 if im and e["precision"] == "day" and im.get("for_date") == e["start"] else None


def table_select(df, key, tickers):
    ev = st.dataframe(df, key=key, on_select="rerun", selection_mode="single-row", hide_index=True, use_container_width=True,
                      column_config=COLS, height=min(36 * (len(df) + 1) + 4, 640))
    rows = (ev.selection or {}).get("rows") or []
    last = st.session_state.get(f"{key}_last")
    if rows and rows[0] != last:
        st.session_state[f"{key}_last"] = rows[0]
        open_card(tickers[rows[0]])
    elif not rows:
        st.session_state[f"{key}_last"] = None


COLS = {
    "When": st.column_config.TextColumn("When", width="medium"),
    "Ticker": st.column_config.TextColumn("Ticker", width="small"),
    "Mkt cap $B": st.column_config.NumberColumn("Mkt cap", format="$%.2fB"),
    "Short % float": st.column_config.NumberColumn("Short % float", format="%.1f%%"),
    "Runway (mo)": st.column_config.NumberColumn("Cash runway", format="%.0f mo", help="Estimated months of cash left at the current burn rate"),
    "Cash short?": st.column_config.TextColumn("Cash out before event?", help="Estimated cash runs out before the catalyst window closes"),
    "Implied ±%": st.column_config.NumberColumn("Implied move", format="±%.0f%%", help="Options-implied move to the first expiry after an exact-date catalyst"),
}

if D:
    tab_cal, tab_scr = st.tabs(["CALENDAR", "SCREENS"])
    with tab_cal:
        st.html('<div class="ct-sec">Upcoming catalysts</div>')
        f1, f2, f3, f4 = st.columns([2.2, 1.2, 1.2, 1.2])
        with f1:
            q = st.text_input("Filter", placeholder="Filter by ticker, company, drug or indication", label_visibility="collapsed")
        with f2:
            win = st.selectbox("Window", [30, 90, 180, 365, 9999], index=2, label_visibility="collapsed",
                               format_func=lambda d: {30: "Next 30 days", 90: "Next 90 days", 180: "Next 6 months", 365: "Next 12 months", 9999: "All upcoming"}[d])
        with f3:
            cap = st.selectbox("Market cap", [0, 300e6, 1e9, 5e9, 20e9], label_visibility="collapsed",
                               format_func=lambda c: "Any market cap" if not c else f"Under ${c / 1e9:g}B" if c >= 1e9 else f"Under ${c / 1e6:.0f}M")
        with f4:
            exact = st.toggle("Exact dates only")
        counts = {t: sum(1 for e in D["events"] if e["type"] == t) for t in TYPES}
        types = st.pills("Event types", [t for t in TYPES if counts[t]], selection_mode="multi", default=[t for t in TYPES if counts[t]],
                         format_func=lambda t: f"{t} ({counts[t]})", label_visibility="collapsed") or []
        rows = []
        for e in D["events"]:
            t = D["tickers"].get(e["ticker"]) or {}
            if e["type"] not in types or (exact and e["precision"] != "day"):
                continue
            if (date.fromisoformat(e["start"]) - today).days > win:
                continue
            if cap and not (t.get("mcap") and t["mcap"] < cap):
                continue
            if q and not any(q.lower() in (x or "").lower() for x in (e["ticker"], e.get("company"), t.get("name"), e.get("drug"), e.get("indication"))):
                continue
            rows.append(e)
        st.caption(f"{len(rows)} events · {len({e['ticker'] for e in rows})} companies · click a row to open the live company card")
        df = pd.DataFrame([{
            "When": e["label"] + (f" ({card.countdown(e, today)})" if e["precision"] == "day" else ""),
            "Ticker": e["ticker"], "Company": (D["tickers"].get(e["ticker"]) or {}).get("name") or e.get("company"),
            "Event": e["type"], "Drug / program": e.get("drug") or "",
            "Mkt cap $B": ((D["tickers"].get(e["ticker"]) or {}).get("mcap") or 0) / 1e9 or None,
            "Short % float": (((D["tickers"].get(e["ticker"]) or {}).get("short") or {}).get("si_float") or 0) * 100 or None,
            "Runway (mo)": runway_months(e["ticker"]), "Cash short?": "⚠ Yes" if cash_before(e["ticker"], e) else "",
            "Implied ±%": implied_for(e["ticker"], e), "Signals": signals(e["ticker"], e)} for e in rows])
        if len(df):
            for c in ("Mkt cap $B", "Short % float", "Runway (mo)", "Implied ±%"):
                df[c] = pd.to_numeric(df[c], errors="coerce")
            table_select(df, "cal", [e["ticker"] for e in rows])
        else:
            st.info("No catalysts match these filters.")

    with tab_scr:
        st.html('<div class="ct-sec">Screens</div><p class="ct-note">Names with an upcoming catalyst that pass each test. Click a row to open the live company card.</p>')
        T = D["tickers"]
        d90 = (today - timedelta(days=90)).isoformat()

        def nxt(tk, maxd=9999, exact_only=False):
            c = [e for e in D["events"] if e["ticker"] == tk and (date.fromisoformat(e["start"]) - today).days <= maxd
                 and (not exact_only or e["precision"] == "day")]
            return min(c, key=lambda e: e["end"]) if c else None

        def screen(title, note, items, extra):
            st.markdown(f"**{title}**")
            st.caption(note)
            if not items:
                st.caption("No names pass right now.")
                return
            df = pd.DataFrame([{"Ticker": tk, "Next catalyst": f"{e['label']} · {e['type']}", **{k: f(tk, e) for k, f in extra.items()}} for tk, e in items])
            table_select(df, "scr_" + title, [tk for tk, _ in items])

        a, b = st.columns(2)
        with a:
            items = sorted([(tk, nxt(tk, 120)) for tk in T if nxt(tk, 120) and ((T[tk].get("short") or {}).get("si_float") or 0) >= .15],
                           key=lambda x: -T[x[0]]["short"]["si_float"])[:15]
            screen("Squeeze watch", "Short interest at or above 15% of float, catalyst inside 120 days.", items,
                   {"Short % float": lambda tk, e: round(T[tk]["short"]["si_float"] * 100, 1), "Days to cover": lambda tk, e: T[tk]["short"].get("dtc")})
            items = sorted([(tk, nxt(tk, 365)) for tk in T if nxt(tk, 365) and (T[tk].get("insiders") or {}).get("buy_90d")],
                           key=lambda x: -T[x[0]]["insiders"]["buy_90d"])[:15]
            screen("Insider buying", "Open-market purchases by officers, directors or 10% owners in the last 90 days.", items,
                   {"Bought, 90 days": lambda tk, e: card.money(T[tk]["insiders"]["buy_90d"]), "Buyers": lambda tk, e: T[tk]["insiders"]["buyers_90d"]})
            items = sorted([(tk, nxt(tk, 120, True)) for tk in T if T[tk].get("implied") and nxt(tk, 120, True)],
                           key=lambda x: -T[x[0]]["implied"]["move_pct"])[:15]
            screen("Biggest implied moves", "Options-implied move to the first expiry after an exact-date catalyst.", items,
                   {"Implied move": lambda tk, e: f"±{T[tk]['implied']['move_pct'] * 100:.0f}%", "Expiry": lambda tk, e: card.fdate(T[tk]["implied"]["expiry"])})
        with b:
            items = sorted([(tk, nxt(tk, 365)) for tk in T if nxt(tk, 365) and T[tk].get("stakes")],
                           key=lambda x: x[1]["end"])[:15]
            screen("New 5%+ holders", "A new Schedule 13D or 13G (not an amendment) filed in the last 90 days.", items,
                   {"Holder": lambda tk, e: (T[tk]["stakes"][0].get("holder") or "")[:28], "% of class": lambda tk, e: T[tk]["stakes"][0].get("pct")})
            items = sorted([(tk, nxt(tk, 365)) for tk in T if nxt(tk, 365) and (cash_before(tk, nxt(tk, 365)) or T[tk].get("dilution"))],
                           key=lambda x: runway_months(x[0]) if runway_months(x[0]) is not None else 99)[:15]
            screen("Cash risk before the event", "Estimated cash runs out before the catalyst window closes, or a share offering was filed in the last 90 days.", items,
                   {"Runway": lambda tk, e: card.runway_label(T[tk].get("runway")), "Offering, 90 days": lambda tk, e: "Yes" if T[tk].get("dilution") else ""})
            items = sorted([(tk, nxt(tk, 120, True)) for tk in T if nxt(tk, 120, True) and (T[tk].get("runway") or {}).get("runs_out")
                            and (date.fromisoformat(T[tk]["runway"]["runs_out"]) - date.fromisoformat(nxt(tk, 120, True)["end"])).days >= 365],
                           key=lambda x: x[1]["start"])[:15]
            screen("Funded through the event", "Exact-date catalyst in the next 120 days and at least 12 months of cash beyond it.", items,
                   {"Runway": lambda tk, e: card.runway_label(T[tk].get("runway")), "Mkt cap": lambda tk, e: card.money(T[tk].get("mcap"))})
        moved = [(e["ticker"], e) for e in D["events"] if e.get("was")][:15]
        screen("Date changes", "Catalysts whose date differs from what the company said in an earlier filing.", moved,
               {"Was": lambda tk, e: e["was"], "Moved": lambda tk, e: e.get("moved")})

st.html('<div class="ct-card"><div class="foot">Catalyst dates come from companies\' own SEC filings and press releases. They\'re read automatically, '
        'so open a company to see the exact quote and link. Short interest is reported by FINRA twice a month. Cash runway is an estimate from the '
        'latest quarterly report. Information only, not investment advice.</div></div>')

# deep link ?t=TICKER, then open any requested card
qt = st.query_params.get("t")
if qt and not st.session_state.get("_deeplinked"):
    st.session_state["_deeplinked"] = True
    open_card(qt.upper())
tk = st.session_state.pop("open_card", None)
if tk:
    show_card(tk)
