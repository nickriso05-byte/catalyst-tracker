"""HTML pieces for the company card, styled like the Catalyst Trader member site (see theme.css)."""
from __future__ import annotations

import html
from datetime import date

CSS = """
<style>
:root{--ct-sans:sofia-pro,"Figtree","Helvetica Neue",Helvetica,Arial,sans-serif;--ct-serif:adobe-caslon-pro,"Libre Caslon Text",Georgia,serif;
--ct-teal:#003e51;--ct-navy:#00263e;--ct-orange:#cd4f17;--ct-grey:#6a6a6a;--ct-line:#dcdcdc;--ct-link:#0046c8;
--ct-gain:#0a7d2c;--ct-loss:#c8102e;--ct-warn:#a14a00;--ct-warn-bg:#fff4e5;--ct-ok:#0a6b2a;--ct-ok-bg:#e9f5ee;--ct-info-bg:#e6eef2}
.ct-top{background:#000;color:#fff;margin:-1rem -1rem 0;padding:14px 18px;display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}
.ct-top .b{font:700 20px/1 var(--ct-sans);letter-spacing:.02em}.ct-top .b span{color:#9fb7c2;font-weight:500}
.ct-top .s{font:400 13px/1.3 var(--ct-sans);color:#c9c9c9}
.ct-sub{background:#cdcdcd;margin:0 -1rem 18px;padding:8px 18px;font:700 14px/1.4 var(--ct-sans);text-transform:uppercase;display:flex;justify-content:space-between;align-items:center}
.ct-sub .pub{background:var(--ct-teal);color:#fff;font:700 14px/1.2 var(--ct-serif);padding:6px 12px;text-transform:uppercase}
.ct-sec{font:700 24px/1.2 var(--ct-sans);text-transform:uppercase;border-bottom:1.5px solid var(--ct-navy);padding-bottom:6px;margin:6px 0 4px}
.ct-note{font:400 14px/1.45 var(--ct-sans);color:var(--ct-grey);margin:0 0 8px}
.ct-card h2{margin:0;font:700 28px/1.15 var(--ct-serif)}
.ct-card .hsub{font:400 15px/1.4 var(--ct-sans);color:var(--ct-grey);margin-top:4px}
.ct-card .dsec{margin-top:20px}
.ct-card .dsec h3{margin:0 0 10px;font:700 15px/1.2 var(--ct-sans);text-transform:uppercase;border-bottom:1.5px solid var(--ct-navy);padding-bottom:6px;display:flex;justify-content:space-between;gap:10px}
.ct-card .dsec h3 small{font-weight:400;text-transform:none;color:var(--ct-grey);font-size:12.5px}
.ct-card .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
.ct-card .stat{border:1px solid var(--ct-line);padding:10px 12px;background:#fff}
.ct-card .stat .k{font:500 12px/1.3 var(--ct-sans);text-transform:uppercase;color:var(--ct-grey)}
.ct-card .stat .v{font:700 22px/1.25 var(--ct-sans);font-variant-numeric:tabular-nums}
.ct-card .stat .s{font:400 12.5px/1.35 var(--ct-sans);color:var(--ct-grey)}
.ct-card .cat{border-left:4px solid var(--ct-teal);padding:8px 12px;margin-bottom:12px;background:#f7f9fa}
.ct-card .cat.new{border-left-color:var(--ct-orange);background:#fff7f2}
.ct-card .cat .t{font:700 15px/1.4 var(--ct-sans)}
.ct-card .cat blockquote{margin:6px 0;padding:0;border:0;font:400 15px/1.5 var(--ct-serif);color:#222}
.ct-card .cat .src{font:400 12.5px/1.4 var(--ct-sans);color:var(--ct-grey)}
.ct-card .callout{padding:10px 12px;font:400 14.5px/1.45 var(--ct-sans);margin-top:10px}
.ct-card .callout.warn{background:var(--ct-warn-bg);color:#5b2a00}.ct-card .callout.ok{background:var(--ct-ok-bg);color:#0b3d1d}
.ct-card .callout.plain{background:#f4f4f4}
.ct-card table{width:100%;border-collapse:collapse;font:400 14px/1.35 var(--ct-sans)}
.ct-card thead tr{background:#000;color:#fff}.ct-card th{text-align:left;padding:7px 8px;font-weight:700;white-space:nowrap}
.ct-card td{padding:7px 8px;vertical-align:top}.ct-card tbody tr:nth-child(even){background:#f1f1f1}
.ct-card .num{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.ct-card .muted{color:var(--ct-grey)}.ct-card .gain{color:var(--ct-gain)}.ct-card .loss{color:var(--ct-loss)}.ct-card .warn{color:var(--ct-warn);font-weight:700}
.ct-card .role{display:block;color:var(--ct-grey);font-size:12.5px}
.ct-card .tag{display:inline-block;font:700 11px/1 var(--ct-sans);text-transform:uppercase;padding:4px 6px;border:1px solid #bcd2dc;color:var(--ct-teal);background:var(--ct-info-bg)}
.ct-card .tag.new{border-color:#f2cfa6;color:var(--ct-warn);background:var(--ct-warn-bg)}
.ct-card .tw{overflow-x:auto}
.ct-card .foot{margin-top:22px;font:400 12.5px/1.5 var(--ct-sans);color:var(--ct-grey);border-top:1px solid var(--ct-line);padding-top:10px}
.ct-card a{color:var(--ct-link)}
</style>
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def money(v):
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.{0 if a >= 1e10 else 1}f}B"
    if a >= 1e6:
        return f"${v / 1e6:.{0 if a >= 1e8 else 1}f}M"
    if a >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"


def pct(v, d=1):
    return "—" if v is None else f"{v * 100:.{d}f}%"


def num(v):
    if v is None:
        return "—"
    v = float(v)
    return f"{v / 1e6:.2f}M" if v >= 1e6 else f"{v / 1e3:.0f}K" if v >= 1e3 else f"{v:.0f}"


def px(v):
    if v is None:
        return "—"
    return f"${v:.0f}" if v >= 100 else f"${v:.2f}" if v >= 1 else f"${v:.3f}"


def fdate(iso):
    if not iso:
        return "—"
    try:
        d = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return esc(iso)
    return f"{d:%b} {d.day}, {d.year}"


def countdown(e, today: date):
    if e.get("precision") != "day":
        return ""
    n = (date.fromisoformat(e["start"]) - today).days
    return "today" if n == 0 else "tomorrow" if n == 1 else f"in {n} days"


EV_NAME = {"PDUFA date": "PDUFA date", "Advisory committee": "advisory committee", "Data readout": "data readout",
           "Regulatory submission": "regulatory submission", "EU decision": "EU decision"}


def runway_label(r):
    if not r:
        return "—"
    if r.get("months") is None:
        return "Cash-flow positive" if r.get("qtr_burn") is not None and r["qtr_burn"] <= 0 else "—"
    return "36+ mo" if r["months"] >= 36 else f"{r['months']:.0f} mo"


def stat(k, v, s=""):
    return f'<div class="stat"><div class="k">{k}</div><div class="v">{v}</div>{f"<div class=s>{s}</div>" if s else ""}</div>'


def head(tk, t):
    chg = t.get("chg_1m")
    chg_s = f' <span class="{"gain" if chg >= 0 else "loss"}">{"+" if chg >= 0 else ""}{pct(chg)} 1 mo</span>' if chg is not None else ""
    return (f'<div class="ct-card"><h2>{esc(tk)} · {esc(t.get("name"))}</h2>'
            f'<div class="hsub">{px(t.get("price"))}{chg_s} · Mkt cap {money(t.get("mcap"))} · Avg vol {num(t.get("avg_vol"))}</div></div>')


def body(tk, t, evs, today: date, as_of_note: str):
    h = ['<div class="ct-card">']
    # catalysts
    h.append(f'<div class="dsec"><h3>Catalysts <small>{len(evs)} upcoming</small></h3>')
    if not evs:
        ne = t.get("next_earnings")
        h.append('<div class="muted">No upcoming FDA date, data readout or submission found in this company\'s SEC filings '
                 f'from the last 5 months or in the FDA calendar feed.{f" Next earnings: {fdate(ne)}." if ne else ""}</div>')
    for e in evs:
        srcs = " · ".join(f'<a href="{esc(s.get("url"))}" target="_blank" rel="noopener">{esc(s.get("kind"))} {fdate(s.get("date"))}</a>'
                          for s in e.get("sources", []))
        h.append(f'<div class="cat{" new" if e.get("new") else ""}"><div class="t">{esc(e["label"])} · {esc(e["type"])}'
                 f'{" · " + esc(e["drug"]) if e.get("drug") else ""} <span class="muted" style="font-weight:400">{countdown(e, today)}</span>'
                 f'{" <span class=\"tag new\">New filing</span>" if e.get("new") else ""}</div>'
                 f'{f"<div class=muted>{esc(e.get("indication"))}</div>" if e.get("indication") else ""}'
                 f'{f"<div class=warn>Moved {esc(e.get("moved"))}: earlier filing said {esc(e.get("was"))}</div>" if e.get("was") else ""}'
                 f'{f"<blockquote>“{esc(e.get("quote"))}”</blockquote>" if e.get("quote") else ""}'
                 f'<div class="src">Source: {srcs}</div></div>')
    h.append("</div>")
    # implied move
    im = t.get("implied")
    if im:
        h.append(f'<div class="dsec"><h3>Options-implied move <small>to the first expiry after {fdate(im["for_date"])}</small></h3><div class="stats">'
                 + stat("Implied move", f"±{im['move_pct'] * 100:.0f}%", f"±{px(im.get('move_usd'))} by {fdate(im.get('expiry'))}")
                 + stat("Implied vol", f"{im['iv'] * 100:.0f}%" if im.get("iv") is not None else "—", "that expiry")
                 + stat("IV rank", f"{im['iv_rank']:.0f}" if im.get("iv_rank") is not None else "—", "vs last 12 months")
                 + '</div><div class="callout plain">This is the size of move the options market is pricing, up or down, by that expiry. '
                   'It is not a forecast of direction.</div></div>')
    # runway
    r = t.get("runway")
    nxt = evs[0] if evs else None
    if r:
        tight = nxt and r.get("runs_out") and r["runs_out"] < nxt["end"]
        burn = "—" if r.get("qtr_burn") is None else ("None" if r["qtr_burn"] <= 0 else money(r["qtr_burn"]))
        h.append(f'<div class="dsec"><h3>Cash runway <small>from the {fdate(r.get("as_of"))} quarter</small></h3><div class="stats">'
                 + stat("Cash &amp; investments", money(r.get("cash")))
                 + stat("Quarterly burn", burn, "operating cash flow, avg of last 2 qtrs")
                 + stat("Runway left", runway_label(r), f"cash out ~{fdate(r['runs_out'])}" if r.get("runs_out") else "")
                 + stat("Debt &amp; leases", money(r.get("debt"))) + "</div>")
        if nxt and r.get("runs_out"):
            nm = EV_NAME.get(nxt["type"], nxt["type"])
            h.append(f'<div class="callout {"warn" if tight else "ok"}">' + (
                f"At the current burn rate, cash may run out (~{fdate(r['runs_out'])}) before the {esc(nm)} window closes ({esc(nxt['label'])}). "
                "Companies in this spot often raise money before or right after the event." if tight else
                f"At the current burn rate, cash lasts past the {esc(nm)} ({esc(nxt['label'])}).") + "</div>")
        h.append("</div>")
    # short interest
    s = t.get("short")
    if s:
        hist = [x for x in (s.get("history") or []) if x[1] is not None]
        base = hist[-4] if len(hist) > 3 else None
        tr = (s["si_float"] - base[1]) if base and s.get("si_float") is not None else None
        h.append(f'<div class="dsec"><h3>Short interest <small>FINRA, as of {fdate(s.get("as_of"))}</small></h3><div class="stats">'
                 + stat("% of float", pct(s.get("si_float")), f"{'up' if tr >= 0 else 'down'} {abs(tr) * 100:.1f} pts since {fdate(base[0])}" if tr is not None else "")
                 + stat("Days to cover", f"{s['dtc']:.1f}" if s.get("dtc") is not None else "—")
                 + stat("Borrow fee", f"{s['fee']:.2f}%" if s.get("fee") is not None else "—", "annualized")
                 + stat("Shares to borrow", num(s.get("available"))) + "</div></div>")
    h.append("</div>")
    return "".join(h)


def tables(t):
    h = ['<div class="ct-card">']
    ins = (t.get("insiders") or {}).get("trades") or []
    h.append('<div class="dsec"><h3>Insider trades <small>open-market buys &amp; sells, Form 4, last 12 months</small></h3>')
    if ins:
        h.append('<div class="tw"><table><thead><tr><th>Date</th><th>Insider</th><th>Type</th><th class="num">Shares</th><th class="num">Price</th><th class="num">Value</th></tr></thead><tbody>')
        for x in ins:
            h.append(f'<tr><td>{fdate(x.get("date"))}</td><td>{esc(x.get("who"))}<span class="role">{esc(x.get("role"))}</span></td>'
                     f'<td class="{"gain" if x["code"] == "P" else "loss"}">{"Buy" if x["code"] == "P" else "Sell"}'
                     f'{" <span class=muted>(10b5-1 plan)</span>" if x.get("planned") else ""}</td>'
                     f'<td class="num">{num(x.get("shares"))}</td><td class="num">{px(x.get("price"))}</td><td class="num">{money(x.get("value"))}</td></tr>')
        h.append("</tbody></table></div>")
    else:
        h.append('<div class="muted">No open-market insider buys or sells in the last 12 months.</div>')
    h.append("</div>")
    st_ = t.get("stakes") or []
    h.append('<div class="dsec"><h3>5%+ holders <small>Schedule 13D / 13G, last 12 months</small></h3>')
    if st_:
        h.append('<div class="tw"><table><thead><tr><th>Filed</th><th>Form</th><th>Holder</th><th class="num">% of class</th><th class="num">Shares</th></tr></thead><tbody>')
        for x in st_:
            h.append(f'<tr><td>{fdate(x.get("date"))}</td><td><a href="{esc(x.get("url"))}" target="_blank" rel="noopener">{esc(x.get("form"))}</a></td>'
                     f'<td>{esc(x.get("holder"))}{" <span class=tag>Biotech fund</span>" if x.get("specialist") else ""}</td>'
                     f'<td class="num">{f"{x["pct"]:.1f}%" if x.get("pct") is not None else "—"}</td><td class="num">{num(x.get("shares"))}</td></tr>')
        h.append("</tbody></table></div>")
    else:
        h.append('<div class="muted">No 13D or 13G filings in the last 12 months.</div>')
    h.append("</div>")
    hd = t.get("holders")
    if hd:
        lst = (hd.get("specialists") or hd.get("top") or [])[:10]
        h.append(f'<div class="dsec"><h3>Institutions <small>13F, quarter ended {fdate(hd.get("quarter"))}</small></h3><div class="stats">'
                 + stat("Holders", hd.get("count")) + stat("% of shares held", pct(hd.get("pct_out"), 0))
                 + stat("New positions", hd.get("new")) + stat("Added / trimmed", f'{hd.get("added")} / {hd.get("trimmed")}') + "</div>"
                 + f'<p class="muted" style="margin:12px 0 6px">{"Biotech specialist funds holding it:" if hd.get("specialists") else "Largest holders:"}</p>'
                 + '<div class="tw"><table><thead><tr><th>Fund</th><th>Last quarter</th><th class="num">% of shares</th><th class="num">Value</th></tr></thead><tbody>'
                 + "".join(f'<tr><td>{esc(x.get("name"))}</td><td>{esc(x.get("status"))}</td><td class="num">{pct(x.get("pct_out"))}</td><td class="num">{money(x.get("value"))}</td></tr>' for x in lst)
                 + "</tbody></table></div></div>")
    dil = t.get("dilution") or []
    if dil:
        h.append('<div class="dsec"><h3>Share-sale filings <small>shelf registrations &amp; prospectuses, last 6 months</small></h3>'
                 + "".join(f'<div><a href="{esc(x.get("url"))}" target="_blank" rel="noopener">{esc(x.get("form"))}</a> · {fdate(x.get("date"))} '
                           f'<span class="muted">{"prospectus for a sale of shares" if str(x.get("form", "")).startswith("424B") else "shelf registration"}</span></div>' for x in dil)
                 + "</div>")
    h.append("</div>")
    return "".join(h)


FOOT = ('<div class="ct-card"><div class="foot">{asof} Sources: company SEC filings (catalyst dates, 13D/13G, share-sale filings), '
        'Unusual Whales (prices, options, short interest from FINRA, Form 4 insider trades, 13F holders, financials). '
        'Catalyst dates are read automatically from filings, so check the source quote. Information only, not investment advice.</div></div>')
