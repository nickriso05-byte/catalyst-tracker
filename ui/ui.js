// FDA Catalyst Tracker UI, mounted by app.py as a Streamlit v2 component.
// Python passes {snap, card, cardTk, refreshing, verdad}. Filters, tab and scroll live here between renders.
// Opening a company sends an "open" trigger; Python answers at once with the saved card, then the live one.
export default function (component) {
  const { data, parentElement: root, setTriggerValue } = component;
  const $ = s => root.querySelector(s);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

  const S = root.__ct || (root.__ct = { wired:false, snapKey:null, active:null, want:null, shown:null, win:180 });
  S.data = data || {};
  S.trigger = setTriggerValue;
  const D = S.data.snap;
  wire();
  if (!D) { $("#groups").innerHTML = `<div class="empty">Loading the catalyst calendar…</div>`; return; }

  const T = D.tickers || {}, U = D.universe || {}, TODAY = new Date(D.today + "T00:00:00"), dayMs = 864e5;
  const VV = S.data.verdad, VR = (VV && VV.rows) || {};
  const PR = {day:0, month:1, quarter:2, half:3, year:4};
  const days = iso => Math.round((new Date(iso + "T00:00:00") - TODAY) / dayMs);

  // ---------- formatting
  const money = v => v == null ? "—" : Math.abs(v) >= 1e9 ? "$" + (v/1e9).toFixed(v >= 1e10 ? 0 : 1) + "B" : Math.abs(v) >= 1e6 ? "$" + (v/1e6).toFixed(v >= 1e8 ? 0 : 1) + "M" : Math.abs(v) >= 1e3 ? "$" + (v/1e3).toFixed(0) + "K" : "$" + v.toFixed(0);
  const pct = (v, d=1) => v == null ? "—" : (v*100).toFixed(d) + "%";
  const num = v => v == null ? "—" : v >= 1e6 ? (v/1e6).toFixed(2) + "M" : v >= 1e3 ? (v/1e3).toFixed(0) + "K" : String(Math.round(v));
  const px = v => v == null ? "—" : "$" + (v >= 100 ? v.toFixed(0) : v >= 1 ? v.toFixed(2) : v.toFixed(3));
  const fdate = iso => { if (!iso) return "—"; const d = new Date(String(iso).slice(0,10) + "T00:00:00"); return d.toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"}); };
  const ftime = ts => ts ? new Date(ts * 1000).toLocaleTimeString("en-US",{hour:"numeric",minute:"2-digit"}) : "";
  const countdown = e => { if (e.precision !== "day") return ""; const n = days(e.start); return n < 0 ? "" : n === 0 ? "Today" : n === 1 ? "Tomorrow" : `In ${n} days`; };
  const groupOf = e => { if (e.precision === "day" || e.precision === "month") { const d = new Date(e.end + "T00:00:00"); return d.toLocaleDateString("en-US",{month:"long",year:"numeric"}); } return e.label; };
  const TYPEC = {"PDUFA date":"#0f6b80","Data readout":"#cd4f17","Advisory committee":"#5b3fa0","Regulatory submission":"#4a5a68","EU decision":"#2e7d4f"};
  const TYPESHORT = {"PDUFA date":"FDA decision","Data readout":"Readout","Advisory committee":"Adcom","Regulatory submission":"Submission","EU decision":"EU decision"};
  const evName = e => ({"PDUFA date":"PDUFA date","Advisory committee":"advisory committee","Data readout":"data readout","Regulatory submission":"regulatory submission","EU decision":"EU decision"}[e.type] || e.type);
  const d90 = new Date(TODAY - 90*dayMs).toISOString().slice(0,10);

  // ---------- per-ticker helpers
  function signals(tk, e){
    const t = T[tk] || {}, out = [];
    if ((t.insiders?.buy_90d || 0) > 0) out.push(["buy", "Insider buy"]);
    const st = (t.stakes || []).find(s => s.date >= d90 && !/\/A$/.test(s.form) && (s.pct || 0) >= 5);
    if (st) out.push(["stake", "New " + st.form]);
    if ((t.dilution || []).length) out.push(["dil", "Offering"]);
    if ((t.short?.si_float || 0) >= 0.2) out.push(["short", "Short " + pct(t.short.si_float, 0)]);
    if (e && e.was) out.push(["moved", "Moved " + e.moved]);
    return out;
  }
  const runwayTxt = r => !r ? null : r.months == null ? (r.qtr_burn != null && r.qtr_burn <= 0 ? "CF+" : null) : r.months >= 36 ? "36+ mo" : r.months.toFixed(0) + " mo";
  const impliedFor = (tk, e) => { const im = T[tk]?.implied; return im && e.precision === "day" && im.for_date === e.start ? im.move_pct : null; };
  const isTop = tk => { const v = T[tk]?.vscore; return v && v.n && v.rank <= Math.ceil(v.n * 0.2); };

  // ---------- KPI strip
  function renderKpis(){
    const ev = D.events;
    const fda30 = ev.filter(e => e.type === "PDUFA date" && e.precision === "day" && days(e.start) >= 0 && days(e.start) <= 30);
    const read90 = ev.filter(e => e.type === "Data readout" && days(e.start) <= 90);
    const top = [...new Set(ev.filter(e => days(e.start) <= 90 && isTop(e.ticker)).map(e => e.ticker))];
    const ins = Object.keys(T).filter(tk => (T[tk].insiders?.buy_90d || 0) > 0);
    const next = fda30.slice().sort((a,b) => a.start.localeCompare(b.start))[0];
    const K = [
      ["FDA decisions · 30 days", fda30.length, next ? `Next: ${next.ticker}, ${fdate(next.start).replace(/, \d{4}/,"")}` : "None in the next 30 days", "#0f6b80"],
      ["Data readouts · 90 days", read90.length, `${new Set(read90.map(e=>e.ticker)).size} companies`, "#cd4f17"],
      ["Top-scored with a catalyst", top.length, top.slice(0,4).join(" · ") || "—", "#0f8a52"],
      ["Insider buying · 90 days", ins.length, ins.sort((a,b)=>T[b].insiders.buy_90d - T[a].insiders.buy_90d).slice(0,4).join(" · ") || "—", "#5b3fa0"],
    ];
    $("#kpis").innerHTML = K.map(([k,v,s,c]) => `<div class="kpi" style="--c:${c}"><div class="k">${esc(k)}</div><div class="v">${v}</div><div class="s">${esc(s)}</div></div>`).join("");
  }

  // ---------- calendar
  const TYPES = ["PDUFA date","Data readout","Advisory committee","Regulatory submission","EU decision"];
  if (!S.active) S.active = new Set(TYPES);
  function renderChips(){
    const counts = {}; D.events.forEach(e => counts[e.type] = (counts[e.type]||0)+1);
    $("#chips").innerHTML = TYPES.filter(t => counts[t]).map(t =>
      `<button class="chip" style="--c:${TYPEC[t]}" aria-pressed="${S.active.has(t)}" data-t="${esc(t)}"><i></i>${esc(t)}<span class="n">${counts[t]}</span></button>`).join("");
  }
  function metric(k, v, opt={}){
    const cls = v == null || v === "—" ? "na" : (opt.warn ? "warn" : "");
    const bar = opt.bar != null ? `<span class="bar"><i style="--w:${Math.max(4, Math.min(100, opt.bar))}%;--bc:${opt.bc || "var(--teal2)"}"></i></span>` : "";
    return `<div class="mt"><span class="k">${k}</span><span class="v ${cls}">${v ?? "—"}</span>${bar}</div>`;
  }
  function evCard(e){
    const t = T[e.ticker] || {}, c = TYPEC[e.type] || "#4a5a68";
    const dt = e.precision === "day" ? new Date(e.start + "T00:00:00") : null;
    const dateBox = dt ? `<div class="date"><span class="d">${dt.getDate()}</span><span class="m">${dt.toLocaleDateString("en-US",{month:"short"})}</span></div>`
                       : `<div class="date win"><span class="d">${esc(e.label)}</span></div>`;
    const cdn = countdown(e), n = e.precision === "day" ? days(e.start) : 99;
    const v = t.vscore, im = impliedFor(e.ticker, e), rw = t.runway, short = t.short?.si_float;
    const cashShort = rw?.runs_out && rw.runs_out < e.end;
    const drug = [e.drug, e.indication].filter(Boolean);
    const tags = signals(e.ticker, e).map(([k,l]) => `<span class="tag ${k}">${esc(l)}</span>`).join("");
    return `<button class="ev" style="--c:${c}" data-tk="${esc(e.ticker)}">
      <div class="ev-top">${dateBox}
        <div class="who"><div class="tkr"><b>${esc(e.ticker)}</b><span class="type" style="--c:${c}">${esc(TYPESHORT[e.type] || e.type)}</span></div>
          <div class="co">${esc(t.name || e.company || "")}</div></div>
        ${cdn ? `<span class="cd ${n <= 7 ? "soon" : ""}">${cdn}</span>` : ""}</div>
      <div class="drug">${drug.length ? esc(drug[0]) + (drug[1] ? ` <em>· ${esc(drug[1])}</em>` : "") : `<em>${esc(e.type)}</em>`}</div>
      <div class="metrics">
        ${metric("Hist. odds", e.base ? Math.round(e.base.p*100) + "%" : null, {bar: e.base ? e.base.p*100 : null})}
        ${metric("Score", v ? v.score : null, {bar: v ? v.score : null, bc: v && isTop(e.ticker) ? "var(--good)" : "var(--teal2)"})}
        ${metric("Implied move", im != null ? "±" + Math.round(im*100) + "%" : null)}
        ${metric("Mkt cap", money(t.mcap))}
        ${metric("Short", short != null ? pct(short, 0) : null)}
        ${metric("Runway", runwayTxt(rw), {warn: cashShort})}
      </div>
      ${tags ? `<div class="tags">${tags}</div>` : ""}
    </button>`;
  }
  function filtered(){
    const q = $("#q").value.trim().toLowerCase(), cap = +$("#cap").value, exact = $("#exact").checked;
    return D.events.filter(e => {
      if (!S.active.has(e.type)) return false;
      if (exact && e.precision !== "day") return false;
      if (days(e.start) > S.win) return false;
      const t = T[e.ticker] || {};
      if (cap && !(t.mcap && t.mcap < cap)) return false;
      if (q && ![e.ticker, e.company, t.name, e.drug, e.indication].some(x => (x||"").toLowerCase().includes(q))) return false;
      return true;
    });
  }
  function renderCal(){
    const rows = filtered(), sort = $("#sort").value;
    $("#calcount").textContent = `${rows.length} events · ${new Set(rows.map(e=>e.ticker)).size} companies`;
    if (!rows.length){ $("#groups").innerHTML = `<div class="empty">No catalysts match these filters.</div>`; return; }
    if (sort !== "date"){
      const key = { score: e => -(T[e.ticker]?.vscore?.score ?? -1), implied: e => -(impliedFor(e.ticker, e) ?? -1), short: e => -(T[e.ticker]?.short?.si_float ?? -1) }[sort];
      const list = rows.slice().sort((a,b) => key(a) - key(b) || a.end.localeCompare(b.end)).slice(0, 120);
      $("#groups").innerHTML = `<div class="group"><div class="grid">${list.map(evCard).join("")}</div></div>`;
      return;
    }
    const groups = []; let cur = null;
    for (const e of rows){ const g = groupOf(e); if (!cur || cur.g !== g){ cur = {g, items:[]}; groups.push(cur); } cur.items.push(e); }
    $("#groups").innerHTML = groups.map(g => `<div class="group"><div class="ghead"><h2>${esc(g.g)}</h2><span>${g.items.length} event${g.items.length>1?"s":""}</span></div>
      <div class="grid">${g.items.map(evCard).join("")}</div></div>`).join("");
  }

  // ---------- any-ticker lookup
  function lookupMatches(q){
    q = q.trim().toUpperCase(); if (!q) return [];
    const all = Object.keys(U), cap = tk => (T[tk]?.mcap || 0);
    const exact = all.filter(tk => tk === q);
    const pre = all.filter(tk => tk !== q && tk.startsWith(q)).sort((a,b) => cap(b) - cap(a));
    const name = q.length >= 3 ? all.filter(tk => !tk.startsWith(q) && (U[tk] || "").toUpperCase().includes(q)).sort((a,b) => cap(b) - cap(a)) : [];
    const out = [...exact, ...pre, ...name];
    if (!out.length && /^[A-Z][A-Z0-9.\-]{0,5}$/.test(q)) out.push(q);
    return out.slice(0, 7);
  }
  function renderLookup(){
    const m = lookupMatches($("#q").value), el = $("#lookup");
    if (!m.length){ el.hidden = true; return; }
    el.hidden = false;
    el.innerHTML = `<div class="lbl">Open a company card</div>` + m.map(tk => `<button data-tk="${esc(tk)}"><b>${esc(tk)}</b>${esc(U[tk] || "")}</button>`).join("");
  }

  // ---------- screens
  function nextEvent(tk, maxDays = 9999, exactOnly = false){
    return D.events.filter(e => e.ticker === tk && days(e.start) <= maxDays && (!exactOnly || e.precision === "day")).sort((a,b) => a.end.localeCompare(b.end))[0];
  }
  function renderScreens(){
    const tks = Object.keys(T);
    const withNext = d => tks.map(tk => [tk, nextEvent(tk, d)]).filter(x => x[1]);
    const newStake = tk => (T[tk].stakes||[]).find(s => s.date >= d90 && !/\/A$/.test(s.form) && (s.pct||0) >= 5);
    const L = [
      ["Top-scored with a catalyst", "Top 20% on the Verdad-style score, with a catalyst in the next 90 days.",
        withNext(90).filter(([tk]) => isTop(tk)).sort((a,b) => T[b[0]].vscore.score - T[a[0]].vscore.score),
        tk => [T[tk].vscore.score, `#${T[tk].vscore.rank} · ${T[tk].tier1?.n ?? 0}/${T[tk].tier1?.n2 ?? 0} funds`]],
      ["FDA decisions this month", "Exact PDUFA dates in the next 30 days, soonest first.",
        tks.map(tk => [tk, D.events.find(e => e.ticker === tk && e.type === "PDUFA date" && e.precision === "day" && days(e.start) >= 0 && days(e.start) <= 30)]).filter(x => x[1]).sort((a,b) => a[1].start.localeCompare(b[1].start)),
        (tk, e) => [impliedFor(tk, e) != null ? "±" + Math.round(impliedFor(tk, e)*100) + "%" : "—", "implied move"]],
      ["Insider buying", "Open-market purchases by officers, directors or 10% owners in the last 90 days.",
        withNext(365).filter(([tk]) => (T[tk].insiders?.buy_90d||0) > 0).sort((a,b) => T[b[0]].insiders.buy_90d - T[a[0]].insiders.buy_90d),
        tk => [money(T[tk].insiders.buy_90d), `${T[tk].insiders.buyers_90d} buyer${T[tk].insiders.buyers_90d>1?"s":""}`]],
      ["New 5%+ holders", "A new Schedule 13D or 13G (not an amendment) in the last 90 days.",
        withNext(365).filter(([tk]) => newStake(tk)).sort((a,b) => newStake(b[0]).date.localeCompare(newStake(a[0]).date)),
        tk => [newStake(tk).pct.toFixed(1) + "%", (newStake(tk).holder || "").slice(0, 26)]],
      ["Cash risk before the event", "Cash may run out before the catalyst window closes, or a share offering was filed in the last 90 days.",
        withNext(365).filter(([tk,e]) => { const r = T[tk].runway; return (r?.runs_out && r.runs_out < e.end) || (T[tk].dilution||[]).length; }).sort((a,b) => (T[a[0]].runway?.months ?? 99) - (T[b[0]].runway?.months ?? 99)),
        tk => [runwayTxt(T[tk].runway) || "—", (T[tk].dilution||[]).length ? "offering filed" : "runway"]],
      ["Funded through the event", "Exact-date catalyst in 120 days and at least 12 months of cash beyond it.",
        tks.map(tk => [tk, nextEvent(tk, 120, true)]).filter(([tk,e]) => e && T[tk].runway?.runs_out && (new Date(T[tk].runway.runs_out) - new Date(e.end)) / dayMs >= 365).sort((a,b) => a[1].start.localeCompare(b[1].start)),
        tk => [runwayTxt(T[tk].runway), money(T[tk].mcap)]],
      ["Heavily shorted into a catalyst", "Short interest at or above 15% of float with a catalyst in 120 days. A risk list: in Verdad's research the most-shorted biotechs returned about −19% a year.",
        withNext(120).filter(([tk]) => (T[tk].short?.si_float||0) >= .15).sort((a,b) => T[b[0]].short.si_float - T[a[0]].short.si_float),
        tk => [pct(T[tk].short.si_float, 0), `${T[tk].short.dtc?.toFixed(1) ?? "—"} days to cover`]],
      ["Date changes", "Catalysts whose date moved from what an earlier filing said.",
        D.events.filter(e => e.was).map(e => [e.ticker, e]),
        (tk, e) => [esc(e.moved), "was " + e.was]],
    ];
    $("#screens").innerHTML = L.map(([h, p, list, val]) => `<div class="screen"><header><h3>${esc(h)}</h3><p>${esc(p)}</p></header>` +
      (list.length ? list.slice(0, 12).map(([tk, e]) => { const [v, s] = val(tk, e);
        return `<button class="srow" data-tk="${esc(tk)}"><b>${esc(tk)}</b><span class="nx">${esc(e.label)} <span>· ${esc(TYPESHORT[e.type] || e.type)}</span></span><span class="val">${v}<small>${esc(s || "")}</small></span></button>`; }).join("")
        : `<div class="none">No names pass right now.</div>`) + `</div>`).join("");
  }

  // ---------- small line charts with hover
  function chart(values, labels, fmt, h, col, area, dotStroke){
    if (!values || values.length < 2) return "";
    const w = 600, p = 4, lo = Math.min(...values), hi = Math.max(...values), span = hi - lo || 1;
    const xy = values.map((v,i) => [p + i*(w-2*p)/(values.length-1), h - p - (v-lo)/span*(h-2*p)]);
    const d = xy.map((q,i) => (i ? "L" : "M") + q[0].toFixed(1) + " " + q[1].toFixed(1)).join("");
    const fill = area ? `<path d="${d} L${xy[xy.length-1][0].toFixed(1)} ${h} L${xy[0][0].toFixed(1)} ${h} Z" fill="${col}" fill-opacity=".14"/>` : "";
    const id = "s" + Math.random().toString(36).slice(2,8);
    setTimeout(() => {
      const el = root.querySelector("#" + id); if (!el) return;
      const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), dot = el.querySelector("circle");
      svg.addEventListener("pointermove", ev => {
        const r = svg.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width * w;
        const i = Math.max(0, Math.min(values.length-1, Math.round((x - p) / ((w-2*p)/(values.length-1)))));
        dot.setAttribute("cx", xy[i][0]); dot.setAttribute("cy", xy[i][1]); dot.style.display = "block";
        tip.style.display = "block"; tip.style.left = (xy[i][0]/w*100) + "%"; tip.style.top = (xy[i][1]/h*r.height) + "px";
        tip.textContent = (labels?.[i] ? labels[i] + " · " : "") + fmt(values[i]);
      });
      svg.addEventListener("pointerleave", () => { tip.style.display = "none"; dot.style.display = "none"; });
    });
    return `<div class="spark-wrap" id="${id}"><svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" style="height:${h}px" role="img" aria-label="trend">
      ${fill}<path d="${d}" fill="none" stroke="${col}" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>
      <circle r="4" fill="${col}" stroke="${dotStroke}" stroke-width="2" style="display:none" vector-effect="non-scaling-stroke"/></svg><div class="tip"></div></div>`;
  }

  // ---------- company card
  const stat = (k, v, s) => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
  const CLOSE = `<button class="close" data-close="1" aria-label="Close">×</button>`;
  function headSkeleton(tk, note){
    const nm = T[tk]?.name || U[tk] || "";
    return `<div class="chead"><div class="row"><div><h2><b>${esc(tk)}</b>${esc(nm)}</h2>
      <div class="live upd"><i></i>${esc(note)}</div></div>${CLOSE}</div></div>
      <div class="cbody"><div class="box full"><div class="skel" style="width:40%"></div><div class="skel"></div><div class="skel" style="width:70%"></div></div>
      <div class="box"><div class="skel"></div><div class="skel" style="width:60%"></div></div><div class="box"><div class="skel"></div><div class="skel" style="width:60%"></div></div></div>`;
  }
  function scoreBox(t){
    const v = t.vscore;
    if (!v) return `<div class="box"><h3>Verdad-style score</h3><div class="muted">Not enough data to score this company (needs 3 of: specialist funds, short interest, insider buying, value).</div></div>`;
    const P = v.parts || {}, t1 = t.tier1 || {}, se = t.value, s = t.short || {}, ins = t.insiders || {};
    const tone = x => x == null ? "neu" : x >= 67 ? "good" : x <= 33 ? "bad" : "neu";
    const pr = x => x == null ? "" : ` <span class="muted">· better than ${x}%</span>`;
    const valTxt = !se ? "No spending data" : se.ev <= 0 ? "Trading below its cash" : `EV ${(se.ev / se.spend).toFixed(1)}× annual R&amp;D + SG&amp;A`;
    return `<div class="box"><h3>Verdad-style score <small>vs. ${v.n || "—"} catalyst companies</small></h3><div class="stats">
      ${stat("Score", v.score + "<span class='muted' style='font-size:14px'> /100</span>", v.rank ? `ranked #${v.rank} of ${v.n}` : "")}
      ${stat("Specialist funds", `${t1.n ?? 0}<span class='muted' style='font-size:14px'> + ${t1.n2 ?? 0}</span>`, "tier-1 + tier-2")}</div>
      ${(t1.funds||[]).length ? `<div class="funds"><b>Tier 1</b>${esc(t1.funds.join(" · "))}</div>` : ""}
      ${(t1.funds2||[]).length ? `<div class="funds"><b>Tier 2</b>${esc(t1.funds2.join(" · "))}</div>` : ""}
      <div class="sigs">
        <div class="sig ${tone(P.spec)}"><b>Specialist funds</b>${t1.n || t1.n2 ? `${t1.n || 0} tier-1, ${t1.n2 || 0} tier-2` : "None of the roster"}${pr(P.spec)}</div>
        <div class="sig ${tone(P.short)}"><b>Short interest</b>${s.si_float != null ? pct(s.si_float) + " of float" : "No data"}${pr(P.short)}</div>
        <div class="sig ${tone(P.ins)}"><b>Insider buying · 90d</b>${ins.buy_90d ? money(ins.buy_90d) + " bought" : "No open-market buys"}${pr(P.ins)}</div>
        <div class="sig ${tone(P.value)}"><b>Value vs. spending</b>${valTxt}${pr(P.value)}</div>
      </div></div>`;
  }
  function hisBox(tk){
    const v = VR[tk];
    if (!VV || !v) return "";
    return `<div class="box"><h3>Colleague's Verdad scan <small>as of ${esc(fdate(VV.asof))}</small></h3><div class="stats">
      ${stat("His score", v.score.toFixed(0), `ranked #${v.rank} of ${VV.n}`)}${stat("His fund count", `${v.tier1 ?? 0} + ${v.tier2 ?? 0}`, "tier-1 + tier-2")}</div>
      ${v.funds ? `<div class="funds">${esc(v.funds)}</div>` : ""}</div>`;
  }
  function renderCard(tk, t, newEvs, meta){
    const evs = [...D.events.filter(e => e.ticker === tk), ...(newEvs || [])].sort((a,b) => a.end.localeCompare(b.end));
    const next = evs[0];
    const r = t.runway, s = t.short, ins = t.insiders || {trades:[]}, h = t.holders, im = t.implied;
    const ch = t.chg_1m;
    const status = meta.live ? `<div class="live"><i></i>Live · ${ftime(meta.fetched)}</div>`
                 : `<div class="live upd"><i></i>${meta.fetched ? "Saved " + ftime(meta.fetched) + " · " : ""}updating live…</div>`;
    const up = t.spark && t.spark.length > 1 && t.spark[t.spark.length-1] >= t.spark[0];
    const sp = t.spark ? chart(t.spark, null, px, 70, up ? "#7fe0a6" : "#ff9a9a", true, "#00263e") : "";
    let html = `<div class="chead"><div class="row"><div><h2><b>${esc(tk)}</b>${esc(t.name || "")}</h2>
        <div class="sub"><span>${px(t.price)}</span>${ch != null ? `<span class="${ch >= 0 ? "up" : "dn"}">${ch >= 0 ? "+" : ""}${pct(ch)} 1 mo</span>` : ""}<span>Mkt cap ${money(t.mcap)}</span><span>Avg vol ${num(t.avg_vol)}</span></div>
        ${status}</div>${CLOSE}</div>${sp ? `<div class="chart">${sp}</div>` : ""}</div><div class="cbody">`;
    html += `<div class="box full"><h3>Catalysts <small>${evs.length} upcoming</small></h3>` + (evs.length ? "" :
      `<div class="muted">No upcoming FDA date, data readout or submission in this company's SEC filings from the last 5 months.${t.next_earnings ? ` Next earnings: ${esc(fdate(t.next_earnings))}.` : ""}</div>`) +
      evs.map(e => `<div class="cat" style="--c:${TYPEC[e.type] || "#4a5a68"}">
        <div class="t">${esc(e.label)} · ${esc(e.type)}${e.drug ? " · " + esc(e.drug) : ""} <span>${countdown(e)}</span>${e.new ? ' <span class="tag new">New filing</span>' : ""}</div>
        <div class="meta">${e.base ? `<span class="pill odds" title="${esc(e.base.source)}. Not drug-specific.">Historically ${Math.round(e.base.p*100)}% ${esc(e.base.what)}</span>` : ""}
          ${e.indication ? `<span class="pill">${esc(e.indication)}</span>` : ""}${e.was ? `<span class="pill">Moved ${esc(e.moved)} · was ${esc(e.was)}</span>` : ""}</div>
        ${e.quote ? `<blockquote>“${esc(e.quote)}”</blockquote>` : ""}
        <div class="src">Source: ${(e.sources||[]).map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.kind)} ${esc(fdate(s.date))}</a>`).join(" · ")}</div></div>`).join("") + `</div>`;
    html += scoreBox(t);
    if (im){
      html += `<div class="box"><h3>Options-implied move <small>to the first expiry after ${esc(fdate(im.for_date))}</small></h3><div class="stats">
        ${stat("Implied move", "±" + (im.move_pct*100).toFixed(0) + "%", "±" + px(im.move_usd) + " by " + fdate(im.expiry))}
        ${stat("Implied vol", im.iv != null ? (im.iv*100).toFixed(0) + "%" : "—", "that expiry")}
        ${stat("IV rank", im.iv_rank != null ? im.iv_rank.toFixed(0) : "—", "vs last 12 months")}</div>
        <div class="callout">The size of move the options market is pricing, up or down, by that expiry. Not a forecast of direction.</div></div>`;
    }
    if (r){
      const tight = next && r.runs_out && r.runs_out < next.end;
      html += `<div class="box"><h3>Cash runway <small>${esc(fdate(r.as_of))} quarter</small></h3><div class="stats">
        ${stat("Cash", money(r.cash))}
        ${stat("Burn / qtr", r.qtr_burn == null ? "—" : r.qtr_burn <= 0 ? "None" : money(r.qtr_burn))}
        ${stat("Runway", r.months == null ? (r.qtr_burn != null && r.qtr_burn <= 0 ? "CF+" : "—") : r.months >= 36 ? "36+ mo" : r.months.toFixed(0) + " mo", r.runs_out ? "to ~" + fdate(r.runs_out) : "")}</div>
        ${next && r.runs_out ? `<div class="callout ${tight ? "warn" : "ok"}">${tight ? `Cash may run out (~${fdate(r.runs_out)}) before the ${esc(evName(next))} window closes (${esc(next.label)}). Companies in this spot often raise money around the event.` : `At the current burn rate, cash lasts past the ${esc(evName(next))} (${esc(next.label)}).`}</div>` : ""}</div>`;
    }
    if (s){
      const hist = (s.history || []).filter(x => x[1] != null), base = hist.length > 3 ? hist[hist.length - 4] : null;
      const tr = base ? s.si_float - base[1] : null;
      html += `<div class="box"><h3>Short interest <small>FINRA, ${esc(fdate(s.as_of))}</small></h3><div class="stats">
        ${stat("% of float", pct(s.si_float), tr != null ? `${tr >= 0 ? "up" : "down"} ${Math.abs(tr*100).toFixed(1)} pts since ${fdate(base[0])}` : "")}
        ${stat("Days to cover", s.dtc != null ? s.dtc.toFixed(1) : "—")}
        ${stat("Borrow fee", s.fee != null ? s.fee.toFixed(1) + "%" : "—")}</div>
        ${hist.length > 2 ? `<div style="margin-top:10px">${chart(hist.map(x => x[1]), hist.map(x => fdate(x[0])), v => pct(v), 70, "#0f6b80", false, "#fff")}</div>` : ""}</div>`;
    }
    html += `<div class="box full"><h3>Insider trades <small>open-market, Form 4, last 12 months</small></h3>` + ((ins.trades||[]).length ? `<div class="tw"><table><thead><tr><th>Date</th><th>Insider</th><th>Type</th><th class="num">Shares</th><th class="num">Price</th><th class="num">Value</th></tr></thead><tbody>` +
      ins.trades.map(x => `<tr><td>${esc(fdate(x.date))}</td><td>${esc(x.who)}<span class="role">${esc(x.role)}</span></td>
        <td class="${x.code === "P" ? "gain" : "loss"}">${x.code === "P" ? "Buy" : "Sell"}${x.planned ? ' <span class="muted">(10b5-1)</span>' : ""}</td>
        <td class="num">${num(x.shares)}</td><td class="num">${px(x.price)}</td><td class="num">${money(x.value)}</td></tr>`).join("") + `</tbody></table></div>`
      : `<div class="muted">No open-market insider buys or sells in the last 12 months.</div>`) + `</div>`;
    html += `<div class="box"><h3>5%+ holders <small>13D / 13G, 12 months</small></h3>` + ((t.stakes||[]).length ? `<div class="tw"><table><thead><tr><th>Filed</th><th>Holder</th><th class="num">%</th></tr></thead><tbody>` +
      t.stakes.map(x => `<tr><td>${esc(fdate(x.date))}<span class="role"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.form)}</a></span></td><td>${esc(x.holder || "")}${x.specialist ? ' <span class="tag stake">Biotech fund</span>' : ""}</td><td class="num">${x.pct != null ? x.pct.toFixed(1) + "%" : "—"}</td></tr>`).join("") + `</tbody></table></div>`
      : `<div class="muted">No 13D or 13G filings in the last 12 months.</div>`) + `</div>`;
    if (h){
      const list = ((h.specialists||[]).length ? h.specialists : (h.top||[])).slice(0, 8);
      html += `<div class="box"><h3>Institutions <small>13F, ${esc(fdate(h.quarter))}</small></h3><div class="stats">
        ${stat("Holders", h.count)}${stat("% held", pct(h.pct_out, 0))}${stat("New / added", `${h.new} / ${h.added}`)}</div>
        <div class="tw" style="margin-top:8px"><table><thead><tr><th>${(h.specialists||[]).length ? "Specialist funds" : "Largest holders"}</th><th>Qtr</th><th class="num">% shares</th></tr></thead><tbody>` +
        list.map(x => `<tr><td>${esc(x.name)}</td><td>${esc(x.status)}</td><td class="num">${pct(x.pct_out)}</td></tr>`).join("") + `</tbody></table></div></div>`;
    }
    if ((t.dilution||[]).length){
      html += `<div class="box"><h3>Share-sale filings <small>last 6 months</small></h3>` +
        t.dilution.map(x => `<div style="margin-bottom:4px"><a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.form)}</a> · ${esc(fdate(x.date))} <span class="muted">${/^424B/.test(x.form) ? "prospectus for a sale of shares" : "shelf registration"}</span></div>`).join("") + `</div>`;
    }
    html += hisBox(tk);
    html += `<div class="cfoot">Sources: company SEC filings (catalyst dates, 13D/13G, share-sale filings), Unusual Whales (prices, options, FINRA short interest, Form 4 insider trades, 13F holders, financials). Historical odds are industry averages by stage (BIO/Informa 2011–2020), not drug-specific. Catalyst dates are read automatically from filings, so check the quote. Information only, not investment advice.</div></div>`;
    return html;
  }

  function showModal(html, keepScroll){
    const m = $("#modal"), y = m.scrollTop;
    $("#sheet").innerHTML = html;
    m.classList.add("on"); $("#scrim").classList.add("on");
    m.scrollTop = keepScroll ? y : 0;
  }
  function openTicker(tk){
    tk = String(tk || "").toUpperCase(); if (!tk) return;
    S.want = tk; S.shown = null;
    $("#lookup").hidden = true;
    showModal(headSkeleton(tk, "Opening…"));
    try { history.replaceState(null, "", "?t=" + encodeURIComponent(tk)); } catch(e) {}
    S.trigger("open", tk);
  }
  function closeTicker(){
    $("#modal").classList.remove("on"); $("#scrim").classList.remove("on");
    S.want = null; S.shown = null;
    try { history.replaceState(null, "", location.pathname); } catch(e) {}
    S.trigger("close", Date.now());
  }

  // ---------- events (wired once)
  function wire(){
    if (S.wired) return; S.wired = true;
    const openFrom = ev => { const r = ev.target.closest("[data-tk]"); if (r) openTicker(r.dataset.tk); };
    $("#groups").addEventListener("click", openFrom);
    $("#screens").addEventListener("click", openFrom);
    $("#lookup").addEventListener("mousedown", ev => ev.preventDefault());   // keep focus so blur doesn't hide it first
    $("#lookup").addEventListener("click", openFrom);
    $("#chips").addEventListener("click", ev => {
      const b = ev.target.closest(".chip"); if (!b) return;
      const t = b.dataset.t; S.active.has(t) ? S.active.delete(t) : S.active.add(t);
      b.setAttribute("aria-pressed", S.active.has(t)); S.renderCal && S.renderCal();
    });
    $("#win").addEventListener("click", ev => {
      const b = ev.target.closest("button"); if (!b) return;
      S.win = +b.dataset.v; root.querySelectorAll("#win button").forEach(x => x.classList.toggle("on", x === b)); S.renderCal && S.renderCal();
    });
    ["#cap","#sort","#exact"].forEach(s => $(s).addEventListener("change", () => S.renderCal && S.renderCal()));
    $("#q").addEventListener("input", () => { S.renderCal && S.renderCal(); S.renderLookup && S.renderLookup(); });
    $("#q").addEventListener("focus", () => S.renderLookup && S.renderLookup());
    $("#q").addEventListener("blur", () => setTimeout(() => { $("#lookup").hidden = true; }, 150));
    $("#q").addEventListener("keydown", ev => { if (ev.key === "Enter" && S.lookupMatches){ const m = S.lookupMatches($("#q").value); if (m.length) openTicker(m[0]); } });
    $("#scrim").addEventListener("click", closeTicker);
    $("#modal").addEventListener("click", ev => { if (ev.target === $("#modal") || ev.target.closest("[data-close]")) closeTicker(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && $("#modal").classList.contains("on")) closeTicker(); });
    root.querySelectorAll(".tab").forEach(b => b.addEventListener("click", () => {
      root.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x === b));
      $("#cal").hidden = b.dataset.tab !== "cal"; $("#scr").hidden = b.dataset.tab !== "scr";
      $("#calcount").style.visibility = b.dataset.tab === "cal" ? "visible" : "hidden";
    }));
    $("#refresh").addEventListener("click", () => S.trigger("refresh", Date.now()));
  }

  // ---------- render
  S.renderCal = renderCal; S.renderLookup = renderLookup; S.lookupMatches = lookupMatches;
  const gen = new Date(D.generated_at);
  const busy = D.phase === "calendar" ? " · adding runway, implied moves, 13D/13G…" : (S.data.refreshing ? " · refreshing…" : "");
  $("#stamp").textContent = "Updated " + gen.toLocaleString("en-US", {month:"short", day:"numeric", hour:"numeric", minute:"2-digit"}) + busy;
  $("#refresh").disabled = !!S.data.refreshing;
  $("#foot").textContent = "Catalyst dates come from companies' own SEC filings and press releases, read automatically; open a company to see the exact quote and link. Short interest is reported by FINRA twice a month. Cash runway is an estimate from the latest quarter. Historical odds are industry averages by stage (BIO/Informa 2011–2020), not drug-specific. Information only, not investment advice.";
  const snapKey = D.generated_at + "|" + D.phase + "|" + D.events.length + "|" + (VV ? VV.n : 0);
  if (S.snapKey !== snapKey){
    D.events.sort((a,b) => a.end.localeCompare(b.end) || PR[a.precision]-PR[b.precision] || a.label.localeCompare(b.label) || a.start.localeCompare(b.start) || a.ticker.localeCompare(b.ticker));
    S.snapKey = snapKey;
    renderKpis(); renderChips(); renderCal(); renderScreens();
  }

  // ---------- company card from Python (saved copy first, then live)
  const c = S.data.card;
  if (!S.want && S.data.cardTk){ S.want = S.data.cardTk; showModal(headSkeleton(S.want, "Opening…")); }   // deep link ?t=
  if (S.want && c && c.tk === S.want){
    const key = [c.tk, c.fetched, c.live, c.loading, c.error].join("|");
    if (S.shown !== key){
      const keep = !!(S.shown && S.shown.startsWith(c.tk + "|"));
      S.shown = key;
      if (c.error) showModal(`<div class="chead"><div class="row"><div><h2><b>${esc(c.tk)}</b></h2></div>${CLOSE}</div></div><div class="cbody"><div class="box full">${esc(c.error)}</div></div>`);
      else if (c.loading) showModal(headSkeleton(c.tk, "Pulling live data…"), keep);
      else if (!c.t) showModal(`<div class="chead"><div class="row"><div><h2><b>${esc(c.tk)}</b></h2></div>${CLOSE}</div></div><div class="cbody"><div class="box full">${esc(c.tk)} isn't a healthcare stock we track, or no data came back for it.</div></div>`);
      else showModal(renderCard(c.tk, c.t, c.new, c), keep);
    }
  }
}
