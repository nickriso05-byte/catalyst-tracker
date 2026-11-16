// FDA Catalyst Tracker UI, mounted by app.py as a Streamlit v2 component.
// Python passes {snap, card, cardTk, refreshing}. The page keeps its own filter/tab/scroll state
// between renders; clicking a company asks Python for a live card ("open" trigger).
export default function (component) {
  const { data, parentElement: root, setTriggerValue } = component;
  const $ = s => root.querySelector(s);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  // one state object per mounted element, kept across re-renders
  const S = root.__ct || (root.__ct = { wired: false, snapKey: null, active: null, want: null, shownCard: null });
  S.data = data || {};
  S.trigger = setTriggerValue;
  const D = S.data.snap;
  if (!D) { $("#rows").innerHTML = `<tr><td colspan="11" class="empty">Loading the catalyst calendar…</td></tr>`; wire(); return; }

  const T = D.tickers || {}, U = D.universe || {}, TODAY = new Date(D.today + "T00:00:00"), dayMs = 864e5;
  const days = iso => Math.round((new Date(iso + "T00:00:00") - TODAY) / dayMs);
  const PR = {day:0, month:1, quarter:2, half:3, year:4};

  // ---------- formatting
  const money = v => v == null ? "—" : Math.abs(v) >= 1e9 ? "$" + (v/1e9).toFixed(v >= 1e10 ? 0 : 1) + "B" : Math.abs(v) >= 1e6 ? "$" + (v/1e6).toFixed(v >= 1e8 ? 0 : 1) + "M" : Math.abs(v) >= 1e3 ? "$" + (v/1e3).toFixed(0) + "K" : "$" + v.toFixed(0);
  const pct = (v, d=1) => v == null ? "—" : (v*100).toFixed(d) + "%";
  const num = v => v == null ? "—" : v >= 1e6 ? (v/1e6).toFixed(2) + "M" : v >= 1e3 ? (v/1e3).toFixed(0) + "K" : String(Math.round(v));
  const px = v => v == null ? "—" : "$" + (v >= 100 ? v.toFixed(0) : v >= 1 ? v.toFixed(2) : v.toFixed(3));
  const fdate = iso => { if (!iso) return "—"; const d = new Date(String(iso).slice(0,10) + "T00:00:00"); return d.toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"}); };
  const countdown = e => { if (e.precision !== "day") return ""; const n = days(e.start); return n === 0 ? "today" : n === 1 ? "tomorrow" : `in ${n} days`; };
  const groupOf = e => { if (e.precision === "day" || e.precision === "month") { const d = new Date(e.end + "T00:00:00"); return d.toLocaleDateString("en-US",{month:"long",year:"numeric"}); } return e.label; };
  const evName = e => ({"PDUFA date":"PDUFA date","Advisory committee":"advisory committee","Data readout":"data readout","Regulatory submission":"regulatory submission","EU decision":"EU decision"}[e.type] || e.type);
  const d90 = new Date(TODAY - 90*dayMs).toISOString().slice(0,10);

  // ---------- table helpers
  function signals(tk){
    const t = T[tk] || {}, out = [];
    if ((t.insiders?.buy_90d || 0) > 0) out.push(["buy", "Insider buy"]);
    const st = (t.stakes || []).find(s => s.date >= d90 && !/\/A$/.test(s.form) && (s.pct || 0) >= 5);
    if (st) out.push(["stake", "New " + st.form]);
    if ((t.dilution || []).some(f => f.date >= d90 && /^424B/.test(f.form))) out.push(["dil", "Offering"]);
    if ((t.short?.si_float || 0) >= 0.2) out.push(["short", "Short " + pct(t.short.si_float, 0)]);
    return out;
  }
  function runwayCell(tk, e){
    const r = T[tk]?.runway;
    if (!r) return `<span class="muted">—</span>`;
    if (r.months == null) return r.qtr_burn != null && r.qtr_burn <= 0 ? `<span class="muted">Cash-flow +</span>` : `<span class="muted">—</span>`;
    const short = e && r.runs_out && r.runs_out < e.end;
    const lbl = r.months >= 36 ? "36+ mo" : r.months.toFixed(0) + " mo";
    return short ? `<span class="warn" title="Cash may run out before this catalyst">${lbl} ⚠</span>` : lbl;
  }
  function impliedCell(tk, e){
    const im = T[tk]?.implied;
    if (!im || e.precision !== "day" || im.for_date !== e.start) return `<span class="muted">—</span>`;
    return "±" + (im.move_pct*100).toFixed(0) + "%";
  }

  // ---------- Verdad-style scan (colleague's daily CSV)
  const VV = S.data.verdad, VR = (VV && VV.rows) || {};
  // our own Verdad-style score (computed every refresh, and live when a card opens)
  function scoreCell(tk){
    const v = T[tk]?.vscore;
    if (!v) return `<span class="muted">—</span>`;
    const top = v.n ? v.rank <= Math.ceil(v.n * 0.2) : false, bottom = v.n ? v.rank > Math.floor(v.n * 0.8) : false;
    return `<span class="score ${top ? "hi" : bottom ? "lo" : ""}" title="Verdad-style score, 0–100. Ranked #${v.rank} of ${v.n} companies with upcoming catalysts.">${v.score}<small>#${v.rank} of ${v.n}</small></span>`;
  }
  const isTop = tk => { const v = T[tk]?.vscore; return v && v.n && v.rank <= Math.ceil(v.n * 0.2); };
  function ourScoreSection(t){
    const v = t.vscore;
    if (!v) return `<div class="dsec"><h3>Verdad-style score</h3><div class="muted">Not enough data to score this company (needs at least 3 of: specialist funds, short interest, insider buying, value).</div></div>`;
    const P = v.parts || {};
    const tone = x => x == null ? "neu" : x >= 67 ? "good" : x <= 33 ? "bad" : "neu";
    const pr = x => x == null ? "" : ` <span class="muted">(better than ${x}%)</span>`;
    const t1 = t.tier1 || {}, se = t.value, s = t.short || {}, ins = t.insiders || {};
    const valTxt = !se ? "No spending data" : se.ev <= 0 ? "Trading below its cash" : `EV ${(se.ev / se.spend).toFixed(1)}× annual R&D + SG&A`;
    return `<div class="dsec"><h3>Verdad-style score <small>vs. the ${v.n || "—"} companies with upcoming catalysts · updates every refresh</small></h3><div class="stats">
      ${stat("Score", v.score + " / 100", v.rank ? `ranked #${v.rank} of ${v.n}` : "")}
      ${stat("Tier-1 specialist funds", t1.n ?? "—", t1.n2 ? `plus ${t1.n2} tier-2` : "no tier-2 funds")}
      ${stat("Roster funds' stake", t1.pct != null ? pct(t1.pct) : "—", "of shares outstanding")}</div>
      ${(t1.funds || []).length ? `<div style="margin-top:8px"><b>Tier 1:</b> <span class="muted">${esc(t1.funds.join(" · "))}</span></div>` : ""}
      ${(t1.funds2 || []).length ? `<div><b>Tier 2:</b> <span class="muted">${esc(t1.funds2.join(" · "))}</span></div>` : ""}
      <div class="sigs">
        <div class="sig ${tone(P.spec)}"><b>Specialist funds</b>${t1.n || t1.n2 ? `${t1.n || 0} tier-1, ${t1.n2 || 0} tier-2` : "None of the roster funds"}${pr(P.spec)}</div>
        <div class="sig ${tone(P.short)}"><b>Short interest</b>${s.si_float != null ? pct(s.si_float) + " of float" : "No data"}${pr(P.short)}</div>
        <div class="sig ${tone(P.ins)}"><b>Insider buying, 90 days</b>${ins.buy_90d ? money(ins.buy_90d) + " bought" : "No open-market buys"}${pr(P.ins)}</div>
        <div class="sig ${tone(P.value)}"><b>Value vs. spending</b>${valTxt}${pr(P.value)}</div>
      </div>
      <div class="callout" style="background:#f4f4f4">Built on the factors Verdad's research found predictive in biotech: ownership by the screener's 39-fund specialist roster (tier-1 funds weighted 100, tier-2 40, matched by SEC filer ID), light short interest, insider buying, and a low price relative to R&D spending. Each is ranked against the companies on the calendar and averaged equally. Peer momentum is left out (Verdad uses clinical-trial similarity, which we don't have). Verdad's results are their own backtests; this score hasn't been backtested yet.</div></div>`;
  }
  // colleague's scan (optional CSV)
  function hisCell(tk){
    const v = VR[tk];
    if (!v) return "";
    return `${v.score.toFixed(0)} (#${v.rank} of ${VV.n})`;
  }
  const SIG = {
    short: {LOW:["good","Lightly shorted"], MID:["neu","Moderately shorted"], HIGH:["bad","Heavily shorted"], NEUTRAL:["neu","No clear short signal"]},
    insider: {BUYING:["good","Insiders net buying"], SELLING:["bad","Insiders net selling"], NEUTRAL:["neu","No insider signal"]},
    peer: {"PEERS-STRONG":["good","Peers trading strong"], "PEERS-MID":["neu","Peers middling"], "PEERS-WEAK":["bad","Peers trading weak"], "NO-PEERS":["neu","No peer group"]},
    value: {CHEAP:["good","Cheap vs. its spending"], FAIR:["neu","Fairly valued vs. spending"], EXPENSIVE:["bad","Expensive vs. spending"], "AT-CASH":["good","Trading near its cash"]},
    cash: {STRONG:["good","Strong balance sheet"], OK:["neu","Adequate balance sheet"], WEAK:["bad","Weak balance sheet"]},
  };
  function verdadSection(tk){
    const v = VR[tk];
    if (!VV) return "";
    if (!v) return `<div class="dsec"><h3>Colleague's Verdad scan <small>not in the latest scan</small></h3><div class="muted">This company isn't in the latest scan of ${VV.n} biotechs.</div></div>`;
    const sig = (k, label, detail) => { const m = (SIG[k]||{})[v[k]]; if (!m) return ""; return `<div class="sig ${m[0]}"><b>${label}</b>${esc(m[1])}${detail ? ` <span class="muted">${detail}</span>` : ""}</div>`; };
    return `<div class="dsec"><h3>Colleague's Verdad scan <small>as of ${esc(fdate(VV.asof))} · ${VV.n} biotechs</small></h3><div class="stats">
      ${stat("Score", v.score.toFixed(0), `ranked #${v.rank} of ${VV.n}`)}
      ${stat("Specialist funds", (v.tier1 ?? 0).toFixed(0), `top-tier funds holding it${v.tier2 ? ` · ${v.tier2.toFixed(0)} second-tier` : ""}`)}</div>
      ${v.funds ? `<div class="muted" style="margin-top:8px">${esc(v.funds)}</div>` : ""}
      <div class="sigs">
        ${sig("short", "Short interest", v.short_float != null ? `(${v.short_float.toFixed(1)}% of float)` : "")}
        ${sig("insider", "Insiders", v.insider_trans != null && v.insider_trans !== 0 ? `(${v.insider_trans > 0 ? "+" : ""}${v.insider_trans.toFixed(1)}% net)` : "")}
        ${sig("peer", "Scientific peers", "")}
        ${sig("value", "Value", v.ev_to_burn != null && v.value !== "AT-CASH" ? `(EV ${v.ev_to_burn.toFixed(1)}× annual burn)` : "")}
        ${sig("cash", "Balance sheet", v.current_ratio != null ? `(current ratio ${v.current_ratio.toFixed(1)})` : "")}
      </div>
      <div class="callout" style="background:#f4f4f4">His screener's score, from its own formula and 39-fund roster (it also uses scientific-peer momentum and second-tier funds).</div></div>`;
  }

  // ---------- calendar
  const TYPES = ["PDUFA date","Advisory committee","Data readout","Regulatory submission","EU decision"];
  if (!S.active) S.active = new Set(TYPES);
  function renderChips(){
    const counts = {}; D.events.forEach(e => counts[e.type] = (counts[e.type]||0)+1);
    $("#chips").innerHTML = TYPES.filter(t => counts[t]).map(t =>
      `<button class="chip" aria-pressed="${S.active.has(t)}" data-t="${esc(t)}">${esc(t)}<span class="n">${counts[t]}</span></button>`).join("");
  }
  function renderCal(){
    const q = $("#q").value.trim().toLowerCase(), win = +$("#win").value, cap = +$("#cap").value, prec = $("#prec").value;
    const rows = D.events.filter(e => {
      if (!S.active.has(e.type)) return false;
      if (prec === "day" && e.precision !== "day") return false;
      if (days(e.start) > win) return false;
      const t = T[e.ticker] || {};
      if (cap && !(t.mcap && t.mcap < cap)) return false;
      if (q && ![e.ticker, e.company, t.name, e.drug, e.indication].some(x => (x||"").toLowerCase().includes(q))) return false;
      return true;
    });
    $("#calcount").textContent = `${rows.length} events · ${new Set(rows.map(e=>e.ticker)).size} companies`;
    let html = "", last = null;
    for (const e of rows){
      const g = groupOf(e);
      if (g !== last){ html += `<tr class="group"><td colspan="11">${esc(g)}</td></tr>`; last = g; }
      const t = T[e.ticker] || {};
      const sig = signals(e.ticker).map(([c,l]) => `<span class="tag ${c}">${esc(l)}</span>`).join("")
        + (e.was ? `<span class="tag moved" title="Earlier filing said ${esc(e.was)}">Moved ${esc(e.moved)}</span>` : "");
      html += `<tr class="ev" data-tk="${esc(e.ticker)}">
        <td class="when">${esc(e.label)}<small>${countdown(e)}</small></td>
        <td><button class="tk" data-tk="${esc(e.ticker)}">${esc(e.ticker)}</button><span class="co">${esc(t.name || e.company)}</span></td>
        <td class="type">${esc(e.type)}</td>
        <td class="drug hide-sm">${esc(e.drug || "")}${e.indication ? `<span class="co">${esc(e.indication)}</span>` : ""}</td>
        <td class="num">${money(t.mcap)}</td>
        <td class="num">${pct(t.short?.si_float)}</td>
        <td class="num">${runwayCell(e.ticker, e)}</td>
        <td class="num hide-sm">${impliedCell(e.ticker, e)}</td>
        <td class="num hide-sm">${e.base ? `<span title="Historically, ${Math.round(e.base.p*100)}% ${esc(e.base.what)}. Source: ${esc(e.base.source)}. Not drug-specific.">${Math.round(e.base.p*100)}%</span>` : `<span class="muted">—</span>`}</td>
        <td class="num hide-sm">${scoreCell(e.ticker)}</td>
        <td class="hide-sm">${sig}</td></tr>`;
    }
    $("#rows").innerHTML = html || `<tr><td colspan="11" class="empty">No catalysts match these filters.</td></tr>`;
  }

  // ---------- any-ticker lookup
  function lookupMatches(q){
    q = q.trim().toUpperCase(); if (!q) return [];
    const all = Object.keys(U);
    const cap = tk => (T[tk]?.mcap || 0);
    const exact = all.filter(tk => tk === q);
    const pre = all.filter(tk => tk !== q && tk.startsWith(q)).sort((a,b) => cap(b) - cap(a));
    const name = q.length >= 3 ? all.filter(tk => !tk.startsWith(q) && (U[tk] || "").toUpperCase().includes(q)).sort((a,b) => cap(b) - cap(a)) : [];
    const out = [...exact, ...pre, ...name];
    if (!out.length && /^[A-Z][A-Z0-9.\-]{0,5}$/.test(q)) out.push(q);   // not in the list: still try it live
    return out.slice(0, 6);
  }
  function renderLookup(){
    const m = lookupMatches($("#q").value), el = $("#lookup");
    if (!m.length){ el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<span class="lbl">Open a company card</span>` + m.map(tk => `<button data-tk="${esc(tk)}"><b>${esc(tk)}</b> ${esc(U[tk] || "")}</button>`).join("");
  }

  // ---------- screens
  function nextEvent(tk, maxDays = 9999, exactOnly = false){
    return D.events.filter(e => e.ticker === tk && days(e.start) <= maxDays && (!exactOnly || e.precision === "day")).sort((a,b) => a.end.localeCompare(b.end))[0];
  }
  function screenTable(list, cols){
    if (!list.length) return `<div class="empty">No names pass right now.</div>`;
    return `<div class="tablewrap"><table><thead><tr><th>Ticker</th><th>Next catalyst</th>${cols.map(c=>`<th class="num">${c[0]}</th>`).join("")}</tr></thead><tbody>` +
      list.map(([tk, e]) => `<tr class="ev" data-tk="${esc(tk)}"><td><button class="tk" data-tk="${esc(tk)}">${esc(tk)}</button></td>
        <td>${esc(e.label)} <span class="muted">${esc(e.type.replace(" date",""))}</span></td>${cols.map(c=>`<td class="num">${c[1](tk, e)}</td>`).join("")}</tr>`).join("") + `</tbody></table></div>`;
  }
  function renderScreens(){
    const tks = Object.keys(T);
    const withNext = d => tks.map(tk => [tk, nextEvent(tk, d)]).filter(x => x[1]);
    const S2 = [];
    S2.push(["Top-scored with a catalyst", "Top 20% on the Verdad-style score, with a catalyst in the next 90 days. Sorted by score.",
      withNext(90).filter(([tk]) => isTop(tk)).sort((a,b) => T[b[0]].vscore.score - T[a[0]].vscore.score).slice(0,15),
      [["Score", tk => scoreCell(tk)], ["Funds (T1 / T2)", tk => T[tk].tier1 ? `${T[tk].tier1.n} / ${T[tk].tier1.n2 || 0}` : "—"], ["Runway", tk => runwayCell(tk)]].concat(VV ? [["His scan", tk => hisCell(tk) || "—"]] : [])]);
    S2.push(["Heavily shorted into a catalyst", "Short interest at or above 15% of float, with a catalyst inside 120 days. Treat as a risk list: in Verdad's research the most-shorted biotechs returned about −19% a year on average.",
      withNext(120).filter(([tk]) => (T[tk].short?.si_float||0) >= .15).sort((a,b) => T[b[0]].short.si_float - T[a[0]].short.si_float).slice(0,15),
      [["Short %", tk => pct(T[tk].short.si_float)], ["Days to cover", tk => T[tk].short.dtc?.toFixed(1) ?? "—"], ["Borrow fee", tk => T[tk].short.fee != null ? T[tk].short.fee.toFixed(1)+"%" : "—"]]]);
    S2.push(["Insider buying", "Open-market purchases by officers, directors or 10% owners in the last 90 days.",
      withNext(365).filter(([tk]) => (T[tk].insiders?.buy_90d||0) > 0).sort((a,b) => T[b[0]].insiders.buy_90d - T[a[0]].insiders.buy_90d).slice(0,15),
      [["Bought (90d)", tk => money(T[tk].insiders.buy_90d)], ["Buyers", tk => T[tk].insiders.buyers_90d]]]);
    const newStake = tk => (T[tk].stakes||[]).find(s => s.date >= d90 && !/\/A$/.test(s.form) && (s.pct||0) >= 5);
    S2.push(["New 5%+ holders", "A new Schedule 13D or 13G (not an amendment) filed in the last 90 days.",
      withNext(365).filter(([tk]) => newStake(tk)).sort((a,b) => newStake(b[0]).date.localeCompare(newStake(a[0]).date)).slice(0,15),
      [["Holder", tk => esc((newStake(tk).holder||"").slice(0,24))], ["% of class", tk => newStake(tk).pct.toFixed(1)+"%"]]]);
    S2.push(["Cash risk before the event", "Estimated cash runs out before the catalyst window closes, or a stock offering was filed in the last 90 days.",
      withNext(365).filter(([tk,e]) => { const r = T[tk].runway; return (r?.runs_out && r.runs_out < e.end) || (T[tk].dilution||[]).length; })
        .sort((a,b) => (T[a[0]].runway?.months ?? 99) - (T[b[0]].runway?.months ?? 99)).slice(0,15),
      [["Runway", tk => runwayCell(tk)], ["Offering 90d", tk => (T[tk].dilution||[]).length ? "Yes" : "—"]]]);
    S2.push(["Funded through the event", "Exact-date catalyst in the next 120 days and at least 12 months of estimated cash beyond it.",
      tks.map(tk => [tk, nextEvent(tk, 120, true)]).filter(([tk,e]) => e && T[tk].runway?.runs_out
        && (new Date(T[tk].runway.runs_out) - new Date(e.end)) / dayMs >= 365).sort((a,b) => a[1].start.localeCompare(b[1].start)).slice(0,15),
      [["Runway", tk => runwayCell(tk)], ["Mkt cap", tk => money(T[tk].mcap)]]]);
    S2.push(["Biggest implied moves", "Options-implied move to the first expiry after an exact-date catalyst.",
      tks.filter(tk => T[tk].implied).map(tk => [tk, D.events.find(e => e.ticker === tk && e.start === T[tk].implied.for_date) || nextEvent(tk)])
        .filter(x => x[1]).sort((a,b) => T[b[0]].implied.move_pct - T[a[0]].implied.move_pct).slice(0,15),
      [["Implied", tk => "±" + (T[tk].implied.move_pct*100).toFixed(0) + "%"], ["Expiry", tk => fdate(T[tk].implied.expiry)]]]);
    S2.push(["Date changes", "Catalysts whose date differs from what the company said in an earlier filing.",
      D.events.filter(e => e.was).map(e => [e.ticker, e]).slice(0,15),
      [["Was", (tk, e) => esc(e.was)], ["Moved", (tk, e) => esc(e.moved)]]]);
    $("#screens").innerHTML = S2.map(([h, p, list, cols]) => `<div class="screen"><h3>${esc(h)}</h3><p>${esc(p)}</p>${screenTable(list, cols)}</div>`).join("");
  }

  // ---------- sparkline (single series line + hover)
  function spark(values, labels, fmt){
    if (!values || values.length < 2) return "";
    const w = 600, h = 56, p = 4, lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const xy = values.map((v,i) => [p + i*(w-2*p)/(values.length-1), h - p - (v-lo)/span*(h-2*p)]);
    const d = xy.map((q,i) => (i ? "L" : "M") + q[0].toFixed(1) + " " + q[1].toFixed(1)).join("");
    const id = "s" + Math.random().toString(36).slice(2,8);
    setTimeout(() => {
      const el = root.querySelector("#" + id); if (!el) return;
      const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), dot = el.querySelector("circle");
      svg.addEventListener("pointermove", ev => {
        const r = svg.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width * w;
        const i = Math.max(0, Math.min(values.length-1, Math.round((x - p) / ((w-2*p)/(values.length-1)))));
        dot.setAttribute("cx", xy[i][0]); dot.setAttribute("cy", xy[i][1]); dot.style.display = "block";
        tip.style.display = "block"; tip.style.left = (xy[i][0]/w*100) + "%"; tip.style.top = (xy[i][1]/h*56) + "px";
        tip.textContent = (labels?.[i] ? labels[i] + " · " : "") + fmt(values[i]);
      });
      svg.addEventListener("pointerleave", () => { tip.style.display = "none"; dot.style.display = "none"; });
    });
    return `<div class="spark-wrap" id="${id}"><svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="trend">
      <path d="${d}" fill="none" stroke="var(--teal)" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
      <circle r="4" fill="var(--teal)" stroke="#fff" stroke-width="2" style="display:none" vector-effect="non-scaling-stroke"/></svg><div class="tip"></div></div>`;
  }

  // ---------- company card (drawer)
  const stat = (k, v, s) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
  function head(tk, sub){
    return `<div class="dhead"><div><h2>${esc(tk)}${U[tk] || T[tk]?.name ? " · " + esc(T[tk]?.name || U[tk]) : ""}</h2><div class="sub">${sub}</div></div>
      <button class="close" data-close="1">Close</button></div>`;
  }
  function renderLoading(tk){
    return head(tk, "Pulling live data…") + `<div class="loading">Checking the latest SEC filings, insider trades, short interest and options for ${esc(tk)}.<div class="bar"></div></div>`;
  }
  function renderError(tk, msg){
    return head(tk, "") + `<div class="loading">${esc(msg)}</div>`;
  }
  function renderTicker(tk, t, newEvs, fetched){
    const evs = [...D.events.filter(e => e.ticker === tk), ...(newEvs || [])].sort((a,b) => a.end.localeCompare(b.end));
    const next = evs[0];
    const r = t.runway, s = t.short, ins = t.insiders || {trades:[]}, h = t.holders, im = t.implied;
    const sub = `${px(t.price)} ${t.chg_1m != null ? `<span style="color:${t.chg_1m>=0?"#7fdc9a":"#ff8a8a"}">${t.chg_1m>=0?"+":""}${pct(t.chg_1m)} 1 mo</span>` : ""} · Mkt cap ${money(t.mcap)} · Avg vol ${num(t.avg_vol)}`;
    let html = `<div class="dhead"><div><h2>${esc(tk)} · ${esc(t.name)}</h2><div class="sub">${sub}</div></div><button class="close" data-close="1">Close</button></div><div class="dbody">`;
    if (t.spark) html += `<div class="dsec"><h3>Price, last 3 months <small>daily close</small></h3>${spark(t.spark, null, px)}</div>`;
    html += `<div class="dsec"><h3>Catalysts <small>${evs.length} upcoming</small></h3>` + (evs.length ? "" :
      `<div class="muted">No upcoming FDA date, data readout or submission found in this company's SEC filings from the last 5 months or in the FDA calendar feed.${t.next_earnings ? ` Next earnings: ${esc(fdate(t.next_earnings))}.` : ""}</div>`) +
      evs.map(e => `<div class="cat${e.new ? " new" : ""}">
        <div class="t">${esc(e.label)} · ${esc(e.type)}${e.drug ? " · " + esc(e.drug) : ""} <span class="muted" style="font-weight:400">${countdown(e)}</span>${e.new ? ' <span class="tag new">New filing</span>' : ""}</div>
        ${e.indication ? `<div class="muted">${esc(e.indication)}</div>` : ""}
        ${e.was ? `<div class="warn">Moved ${esc(e.moved)}: earlier filing said ${esc(e.was)}</div>` : ""}
        ${e.base ? `<div class="muted">Historical average: ${Math.round(e.base.p*100)}% ${esc(e.base.what)} <span style="font-size:12px">(${esc(e.base.source)}; not drug-specific odds)</span></div>` : ""}
        ${e.quote ? `<blockquote>“${esc(e.quote)}”</blockquote>` : ""}
        <div class="src">Source: ${(e.sources||[]).map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.kind)} ${esc(fdate(s.date))}</a>`).join(" · ")}</div></div>`).join("") + `</div>`;
    html += ourScoreSection(t) + verdadSection(tk);
    if (im){
      html += `<div class="dsec"><h3>Options-implied move <small>to the first expiry after ${esc(fdate(im.for_date))}</small></h3><div class="stats">
        ${stat("Implied move", "±" + (im.move_pct*100).toFixed(0) + "%", "±" + px(im.move_usd) + " by " + fdate(im.expiry))}
        ${stat("Implied vol", im.iv != null ? (im.iv*100).toFixed(0) + "%" : "—", "that expiry")}
        ${stat("IV rank", im.iv_rank != null ? im.iv_rank.toFixed(0) : "—", "vs last 12 months")}</div>
        <div class="callout" style="background:#f4f4f4">This is the size of move the options market is pricing, up or down, by that expiry. It is not a forecast of direction.</div></div>`;
    }
    if (r){
      const tight = next && r.runs_out && r.runs_out < next.end;
      html += `<div class="dsec"><h3>Cash runway <small>from the ${esc(fdate(r.as_of))} quarter</small></h3><div class="stats">
        ${stat("Cash &amp; investments", money(r.cash))}
        ${stat("Quarterly burn", r.qtr_burn == null ? "—" : r.qtr_burn <= 0 ? "None" : money(r.qtr_burn), "operating cash flow, avg of last 2 qtrs")}
        ${stat("Runway left", r.months == null ? (r.qtr_burn != null && r.qtr_burn <= 0 ? "Cash-flow +" : "—") : r.months >= 36 ? "36+ mo" : r.months.toFixed(0) + " mo", r.runs_out ? "cash out ~" + fdate(r.runs_out) : "")}
        ${stat("Debt &amp; leases", money(r.debt))}</div>
        ${next && r.runs_out ? `<div class="callout ${tight ? "warn" : "ok"}">${tight ? `At the current burn rate, cash may run out (~${fdate(r.runs_out)}) before the ${esc(evName(next))} window closes (${esc(next.label)}). Companies in this spot often raise money before or right after the event.` : `At the current burn rate, cash lasts past the ${esc(evName(next))} (${esc(next.label)}).`}</div>` : ""}</div>`;
    }
    if (s){
      const hist = (s.history || []).filter(x => x[1] != null), base = hist.length > 3 ? hist[hist.length - 4] : null;
      const tr = base ? s.si_float - base[1] : null;
      html += `<div class="dsec"><h3>Short interest <small>FINRA, as of ${esc(fdate(s.as_of))}</small></h3><div class="stats">
        ${stat("% of float", pct(s.si_float), tr != null ? `${tr >= 0 ? "up" : "down"} ${Math.abs(tr*100).toFixed(1)} pts since ${fdate(base[0])}` : "")}
        ${stat("Days to cover", s.dtc != null ? s.dtc.toFixed(1) : "—")}
        ${stat("Borrow fee", s.fee != null ? s.fee.toFixed(2) + "%" : "—", "annualized")}
        ${stat("Shares to borrow", num(s.available))}</div>
        ${hist.length > 2 ? `<div style="margin-top:10px">${spark(hist.map(x => x[1]), hist.map(x => fdate(x[0])), v => pct(v))}</div>` : ""}</div>`;
    }
    html += `<div class="dsec"><h3>Insider trades <small>open-market buys &amp; sells, Form 4, last 12 months</small></h3>`;
    html += (ins.trades||[]).length ? `<div class="tablewrap"><table class="dtable"><thead><tr><th>Date</th><th>Insider</th><th>Type</th><th class="num">Shares</th><th class="num">Price</th><th class="num">Value</th></tr></thead><tbody>` +
      ins.trades.map(x => `<tr><td>${esc(fdate(x.date))}</td><td>${esc(x.who)}<span class="role">${esc(x.role)}</span></td>
        <td class="${x.code === "P" ? "gain" : "loss"}">${x.code === "P" ? "Buy" : "Sell"}${x.planned ? ' <span class="muted">(10b5-1 plan)</span>' : ""}</td>
        <td class="num">${num(x.shares)}</td><td class="num">${px(x.price)}</td><td class="num">${money(x.value)}</td></tr>`).join("") + `</tbody></table></div>`
      : `<div class="muted">No open-market insider buys or sells in the last 12 months.</div>`;
    html += `</div><div class="dsec"><h3>5%+ holders <small>Schedule 13D / 13G, last 12 months</small></h3>`;
    html += (t.stakes||[]).length ? `<div class="tablewrap"><table class="dtable"><thead><tr><th>Filed</th><th>Form</th><th>Holder</th><th class="num">% of class</th><th class="num">Shares</th></tr></thead><tbody>` +
      t.stakes.map(x => `<tr><td>${esc(fdate(x.date))}</td><td><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.form)}</a></td>
        <td>${esc(x.holder || "")}${x.specialist ? ' <span class="tag stake">Biotech fund</span>' : ""}</td><td class="num">${x.pct != null ? x.pct.toFixed(1) + "%" : "—"}</td><td class="num">${num(x.shares)}</td></tr>`).join("") + `</tbody></table></div>`
      : `<div class="muted">No 13D or 13G filings in the last 12 months.</div>`;
    html += `</div>`;
    if (h){
      html += `<div class="dsec"><h3>Institutions <small>13F, quarter ended ${esc(fdate(h.quarter))}</small></h3><div class="stats">
        ${stat("Holders", h.count)}${stat("% of shares held", pct(h.pct_out, 0))}${stat("New positions", h.new)}${stat("Added / trimmed", h.added + " / " + h.trimmed)}</div>`;
      const list = ((h.specialists||[]).length ? h.specialists : (h.top||[])).slice(0, 10);
      html += `<p class="muted" style="margin:12px 0 6px">${(h.specialists||[]).length ? "Biotech specialist funds holding it:" : "Largest holders:"}</p>
        <div class="tablewrap"><table class="dtable"><thead><tr><th>Fund</th><th>Last quarter</th><th class="num">% of shares</th><th class="num">Value</th></tr></thead><tbody>` +
        list.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(x.status)}</td><td class="num">${pct(x.pct_out)}</td><td class="num">${money(x.value)}</td></tr>`).join("") + `</tbody></table></div></div>`;
    }
    if ((t.dilution||[]).length){
      html += `<div class="dsec"><h3>Share-sale filings <small>shelf registrations &amp; prospectuses, last 6 months</small></h3>` +
        t.dilution.map(x => `<div><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.form)}</a> · ${esc(fdate(x.date))} <span class="muted">${/^424B/.test(x.form) ? "prospectus for a sale of shares" : "shelf registration"}</span></div>`).join("") + `</div>`;
    }
    const ft = fetched ? new Date(fetched * 1000).toLocaleTimeString("en-US", {hour:"numeric", minute:"2-digit"}) : "";
    html += `<div class="foot">${ft ? `Live as of ${ft}. ` : ""}Sources: company SEC filings (catalyst dates, 13D/13G, share-sale filings), Unusual Whales (prices, options, short interest from FINRA, Form 4 insider trades, 13F holders, financials). Catalyst dates are read automatically from filings, so check the source quote. Information only, not investment advice.</div></div>`;
    return html;
  }

  function openDrawer(html){
    const dr = $("#drawer"); dr.innerHTML = html; dr.classList.add("on"); dr.setAttribute("aria-hidden","false"); $("#scrim").classList.add("on");
  }
  function openTicker(tk){
    tk = String(tk || "").toUpperCase(); if (!tk) return;
    S.want = tk; S.shownCard = null;
    openDrawer(renderLoading(tk)); $("#drawer").scrollTop = 0;
    try { history.replaceState(null, "", "?t=" + encodeURIComponent(tk)); } catch(e) {}
    S.trigger("open", tk);
  }
  function closeTicker(){
    $("#drawer").classList.remove("on"); $("#drawer").setAttribute("aria-hidden","true"); $("#scrim").classList.remove("on");
    S.want = null; S.shownCard = null;
    try { history.replaceState(null, "", location.pathname); } catch(e) {}
    S.trigger("close", Date.now());
  }

  // ---------- events (wired once)
  function wire(){
    if (S.wired) return; S.wired = true;
    const openFrom = ev => { const r = ev.target.closest("[data-tk]"); if (r) openTicker(r.dataset.tk); };
    $("#rows").addEventListener("click", openFrom);
    $("#screens").addEventListener("click", openFrom);
    $("#lookup").addEventListener("click", openFrom);
    $("#chips").addEventListener("click", ev => {
      const b = ev.target.closest(".chip"); if (!b) return;
      const t = b.dataset.t; S.active.has(t) ? S.active.delete(t) : S.active.add(t);
      b.setAttribute("aria-pressed", S.active.has(t)); S.renderCal && S.renderCal();
    });
    ["#q","#win","#cap","#prec"].forEach(s => $(s).addEventListener("input", () => { S.renderCal && S.renderCal(); S.renderLookup && S.renderLookup(); }));
    $("#q").addEventListener("keydown", ev => { if (ev.key === "Enter" && S.lookupMatches){ const m = S.lookupMatches($("#q").value); if (m.length) openTicker(m[0]); } });
    $("#scrim").addEventListener("click", closeTicker);
    $("#drawer").addEventListener("click", ev => { if (ev.target.closest("[data-close]")) closeTicker(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#drawer").classList.contains("on")) closeTicker(); });
    root.querySelectorAll(".tab").forEach(b => b.addEventListener("click", () => {
      root.querySelectorAll(".tab").forEach(x => x.setAttribute("aria-selected", x === b));
      $("#cal").hidden = b.dataset.tab !== "cal"; $("#scr").hidden = b.dataset.tab !== "scr";
    }));
    $("#stamp").addEventListener("click", ev => { if (ev.target.closest(".refresh")) S.trigger("refresh", Date.now()); });
  }

  // ---------- render
  S.renderCal = renderCal; S.renderLookup = renderLookup; S.lookupMatches = lookupMatches;
  wire();
  const gen = new Date(D.generated_at);
  const busy = D.phase === "calendar" ? " · adding runway, implied moves, 13D/13G…" : (S.data.refreshing ? " · refreshing…" : "");
  $("#stamp").innerHTML = "Updated " + esc(gen.toLocaleString("en-US", {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"})) + esc(busy)
    + `<button class="refresh" ${S.data.refreshing ? "disabled" : ""} title="Re-check SEC filings and market data now">Refresh</button>`;
  $("#foot").innerHTML = `Catalyst dates come from companies' own SEC filings and press releases. They're read automatically, so open a company to see the exact quote and link.
    Short interest is reported by FINRA twice a month. Cash runway is an estimate from the latest quarterly report. Information only, not investment advice.`;
  const snapKey = D.generated_at + "|" + D.phase + "|" + D.events.length;
  if (S.snapKey !== snapKey){
    D.events.sort((a,b) => a.end.localeCompare(b.end) || PR[a.precision]-PR[b.precision] || a.label.localeCompare(b.label) || a.start.localeCompare(b.start) || a.ticker.localeCompare(b.ticker));
    S.snapKey = snapKey;
    renderChips(); renderCal(); renderScreens(); renderLookup();
  }

  // live card from Python
  const c = S.data.card;
  const wantTk = S.want || S.data.cardTk;
  if (wantTk && !S.want && S.data.cardTk) { S.want = S.data.cardTk; openDrawer(renderLoading(S.want)); }   // deep link ?t=
  if (S.want && c && c.tk === S.want){
    const k = c.tk + "|" + c.fetched;
    if (S.shownCard !== k){
      S.shownCard = k;
      const dr = $("#drawer"), y = dr.scrollTop;
      openDrawer(c.error ? renderError(c.tk, c.error) : !c.t ? renderError(c.tk, `${c.tk} isn't a healthcare stock we track, or no data came back for it.`) : renderTicker(c.tk, c.t, c.new, c.fetched));
      dr.scrollTop = y;
    }
  }
}
