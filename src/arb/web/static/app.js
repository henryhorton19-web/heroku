/* Arb console — a small hash-routed SPA over /api/v1.
   No framework and no build step: this is a read-mostly console over a local SQLite
   file, and a node toolchain would be a second thing to maintain for a page one
   person opens. Charts are hand-drawn SVG for the same reason -- a CDN would make an
   offline tool depend on the network. */
const $ = (s, r = document) => r.querySelector(s);
const money = p => "£" + (p / 100).toFixed(2);
const pct = v => (v * 100).toFixed(1) + "%";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts) {
  const r = await fetch("/api/v1" + path, {
    headers: { "Content-Type": "application/json" }, ...opts,
  });
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail ?? detail; } catch (_) { /* keep status */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return r.json();
}

/* Measured vs assumed is the only thing colour means here. */
const mark = m => (m === true ? "measured" : m === false ? "assumed" : "");
const badge = (kind, text) => `<span class="badge ${kind}">${esc(text)}</span>`;

const ROUTES = {};
const nav = [
  ["dashboard", "◈", "Dashboard"], ["scanner", "◎", "Scanner"],
  ["inventory", "▣", "Inventory"], ["books", "≡", "Books"],
  ["tax", "£", "Tax"], ["reconcile", "⇄", "Reconcile"],
  ["labels", "▤", "Labels"], ["provenance", "◐", "Provenance"],
];

function shell() {
  const here = (location.hash || "#/dashboard").slice(2);
  $("#nav").innerHTML = nav.map(([k, ic, label]) =>
    `<a href="#/${k}" class="${here === k ? "on" : ""}">
       <span class="ic">${ic}</span>${label}</a>`).join("");
}

function loading() { $("#view").innerHTML = `<div class="empty"><span class="spin"></span></div>`; }
function failed(e) {
  $("#view").innerHTML = `<div class="dangerbox"><b>Could not load.</b> ${esc(e.message)}
    <div class="note">The API is served from the same process as this page; if it is
    unreachable the server has stopped.</div></div>`;
}

async function route() {
  shell();
  const key = (location.hash || "#/dashboard").slice(2).split("?")[0];
  const view = ROUTES[key] || ROUTES.dashboard;
  loading();
  try { await view(); } catch (e) { failed(e); }
}

/* ------------------------------------------------------------------ dashboard */
ROUTES.dashboard = async () => {
  const d = await api("/dashboard");
  const maxCount = Math.max(1, ...d.pipeline.map(s => s.count));
  const metrics = d.metrics.map(m => `
    <div class="card metric">
      <span class="l">${esc(m.label)}</span>
      <span class="v ${mark(m.measured)}">${esc(m.value)}</span>
      <span class="n">${esc(m.note ?? "")}</span>
    </div>`).join("");
  const stages = d.pipeline.map(s => `
    <div class="stage ${s.derived ? "derived" : ""}">
      <span class="sn">${esc(s.label)}</span>
      <span class="sc">${s.count}</span>
      ${s.cost_pence ? `<span class="sv">${money(s.cost_pence)}</span>` : ""}
      <div class="bar" style="width:${Math.round((s.count / maxCount) * 100)}%"></div>
    </div>`).join("");
  const verts = d.verticals.length ? `<table>
      <thead><tr><th>Category</th><th>Country</th><th class="n">Listings</th>
      <th class="n">Median net</th><th class="n">Avg watchers</th></tr></thead><tbody>
      ${d.verticals.map(v => `<tr><td>${esc(v.category_id)}</td><td>${esc(v.country ?? "—")}</td>
        <td class="n">${v.listings}</td>
        <td class="n">${v.median_net_pence == null ? "—" : money(v.median_net_pence)}</td>
        <td class="n">${v.avg_contest == null ? "—" : v.avg_contest.toFixed(1)}</td></tr>`).join("")}
      </tbody></table>
      <p class="note">Read margin against watchers. A high-margin niche with many
      watchers is one you lose races in.</p>`
    : `<div class="empty">No verticals yet — these build up from scans.</div>`;

  $("#view").innerHTML = `
    <div class="head"><div><h1>Dashboard</h1>
      <p>Teal is measured against settlement data. Amber is computed from an assumption
      nobody has checked. Nothing else is coloured.</p></div>
      ${badge(d.open_placeholders ? "warn" : "ok",
        `${d.open_placeholders} assumptions open`)}</div>
    ${d.synthetic_trades ? `<div class="warnbox"><b>${d.synthetic_trades} trades on this
      page are seeded, not traded.</b> They give the page shape before the first real
      sale and are excluded from the provenance register — seeding cannot close a
      placeholder.</div>` : ""}
    <div class="grid g3">${metrics}</div>
    <div class="card"><h2>Pipeline</h2><div class="pipe">${stages}</div>
      <p class="note">Six stored stages plus one derived: an item is <i>sold</i> when the
      buyer pays and <i>funds cleared</i> when settlement data arrives, which is a fact
      about the fee record rather than a state anyone sets.</p></div>
    <div class="card"><h2>Verticals</h2>${verts}</div>`;
};

/* ------------------------------------------------------------------ scanner */
let scanState = { minNet: 0, reasons: [] };

ROUTES.scanner = async () => {
  if (!scanState.reasons.length) scanState.reasons = await api("/skip-reasons");
  const opps = await api(`/opportunities?limit=100&min_net_pence=${scanState.minNet}`);
  const cards = opps.length ? opps.map(o => `
    <div class="opp">
      <div class="t">${esc(o.title || "(untitled)")}</div>
      <div class="m">
        <div><span class="k">NET</span><br><span class="val ${o.net_pence > 0 ? "" : ""}">${money(o.net_pence)}</span></div>
        <div><span class="k">VEL</span><br><span class="val">${o.capital_velocity == null ? "—" : o.capital_velocity.toFixed(4)}</span></div>
        <div><span class="k">CONF</span><br><span class="val">${o.est_confidence.toFixed(2)}</span></div>
        <div><span class="k">ROI</span><br><span class="val">${pct(o.roi)}</span></div>
      </div>
      <div class="chips">
        ${badge("mute", `cost ${money(o.price_pence)}`)}
        ${badge("mute", `${o.comp_n} comps`)}
        ${o.favourites != null ? badge("mute", `${o.favourites} watching`) : ""}
        ${badge("warn", o.fee_table_version)}
      </div>
      <div class="acts">
        <button class="buy" data-buy="${o.id}">Buy</button>
        <button class="skip" data-skip="${o.id}">Skip</button>
      </div>
    </div>`).join("")
    : `<div class="empty">No opportunities scored yet. Run <code>arb scan</code>.</div>`;

  $("#view").innerHTML = `
    <div class="head"><div><h1>Scanner</h1>
      <p>Ranked by capital velocity, never by ROI. Ordering is computed server-side so
      the list you see is the list the buy side produced.</p></div></div>
    <div class="card"><div class="grid g3">
      <div><label class="f">Minimum net profit — £<span id="mnv">${(scanState.minNet/100).toFixed(2)}</span></label>
        <input type="range" id="mn" min="0" max="5000" step="100" value="${scanState.minNet}"></div>
    </div></div>
    <div class="grid g3">${cards}</div>`;

  $("#mn").oninput = e => { $("#mnv").textContent = (e.target.value/100).toFixed(2); };
  $("#mn").onchange = e => { scanState.minNet = +e.target.value; route(); };
  document.querySelectorAll("[data-buy]").forEach(b =>
    b.onclick = () => decisionModal(+b.dataset.buy, "bought"));
  document.querySelectorAll("[data-skip]").forEach(b =>
    b.onclick = () => decisionModal(+b.dataset.skip, "skipped"));
};

function decisionModal(id, outcome) {
  const isSkip = outcome === "skipped";
  const body = isSkip
    ? `<label class="f">Why are you passing?</label>
       <select id="reason">${scanState.reasons.map(r =>
         `<option value="${r}">${esc(r.replace(/_/g, " "))}</option>`).join("")}</select>
       <p class="note">A reason is required. AutoBuy's dry-run scores itself by diffing
       against these rows; without one it has nothing to compare and flatters the
       automation by default.</p>`
    : `<label class="f">What did you actually pay? (£)</label>
       <input type="text" id="spend" placeholder="12.50" inputmode="decimal">
       <p class="note">Required. Without a cost basis every downstream margin is
       overstated.</p>`;
  const veil = document.createElement("div");
  veil.className = "veil";
  veil.innerHTML = `<div class="modal" role="dialog" aria-modal="true">
      <h3>${isSkip ? "Skip" : "Buy"} opportunity ${id}</h3>
      <div class="sub">This is recorded permanently in the decisions ledger.</div>
      ${body}<div id="err"></div>
      <div class="row"><button id="cancel">Cancel</button>
      <button class="primary" id="ok">Record ${isSkip ? "skip" : "purchase"}</button></div>
    </div>`;
  document.body.appendChild(veil);
  const close = () => veil.remove();
  $("#cancel", veil).onclick = close;
  veil.onclick = e => { if (e.target === veil) close(); };
  $("#ok", veil).onclick = async () => {
    const payload = { opportunity_id: id, outcome };
    if (isSkip) payload.skip_reason = $("#reason", veil).value;
    else payload.spend = $("#spend", veil).value;
    try { await api("/decisions", { method: "POST", body: JSON.stringify(payload) });
      close(); route();
    } catch (e) {
      $("#err", veil).innerHTML = `<div class="dangerbox" style="margin-top:.9rem">${esc(e.message)}</div>`;
    }
  };
}

/* ------------------------------------------------------------------ inventory */
let invFilter = "";
ROUTES.inventory = async () => {
  const [rows, hz] = await Promise.all([
    api("/inventory" + (invFilter ? `?state=${invFilter}` : "")), api("/hazards"),
  ]);
  const states = ["", "scouted", "sniped", "in_transit", "enhanced", "listed", "sold"];
  const hazardRows = hz.hazards.map(h => `
    <tr><td>${h.inventory_id}</td><td>${esc(h.venue)}:${esc(h.external_id)}</td>
    <td>${badge(h.kind === "delist_pending" ? "warn" : "bad", h.kind.replace(/_/g, " "))}</td>
    <td>${h.kind === "live_after_sale"
      ? `<button data-delist="${h.inventory_id}" data-keep="${esc(h.venue)}">Request de-list</button>`
      : esc(h.detail ?? "—")}</td></tr>`).join("");

  $("#view").innerHTML = `
    <div class="head"><div><h1>Inventory</h1>
      <p>Owned stock and anything at risk of being sold twice.</p></div>
      ${badge(hz.hazards.length ? "bad" : "ok",
        hz.hazards.length ? `${hz.hazards.length} hazards` : "no hazards")}</div>
    ${hz.hazards.length ? `<div class="card">
      <h2>Double-sale hazards</h2>
      <div class="dangerbox">De-listing is a request, not a result. The venue call fails
      independently, so a listing stays a hazard until that venue confirms — nothing
      here reports success on click.</div>
      <table><thead><tr><th>Item</th><th>Listing</th><th>State</th><th>Action</th></tr></thead>
      <tbody>${hazardRows}</tbody></table>
      <p class="note">${hz.in_flight} de-list requests in flight.</p></div>` : ""}
    <div class="card">
      <h2>Stock</h2>
      <div class="chips" style="margin-bottom:.9rem">${states.map(s =>
        `<button class="chip ${invFilter === s ? "on" : ""}" data-st="${s}">${s ? esc(s.replace(/_/g," ")) : "all"}</button>`).join("")}</div>
      ${rows.length ? `<table><thead><tr><th>#</th><th>State</th><th class="n">Cost</th>
        <th class="n">Gross</th><th class="n">Age</th><th>Fees</th></tr></thead><tbody>
        ${rows.map(r => `<tr><td>${r.id}${r.synthetic ? " " + badge("warn","seeded") : ""}</td>
          <td>${esc(r.state.replace(/_/g," "))}</td>
          <td class="n">${money(r.cost_pence)}</td>
          <td class="n">${r.gross_pence == null ? "—" : money(r.gross_pence)}</td>
          <td class="n">${r.age_days}d</td>
          <td>${r.settled ? badge("ok","settled") : badge("warn","estimated")}</td></tr>`).join("")}
        </tbody></table>` : `<div class="empty">No stock recorded.</div>`}
    </div>`;
  document.querySelectorAll("[data-st]").forEach(b =>
    b.onclick = () => { invFilter = b.dataset.st; route(); });
  document.querySelectorAll("[data-delist]").forEach(b => b.onclick = async () => {
    await api(`/hazards/${b.dataset.delist}/request-delist?keep_venue=${encodeURIComponent(b.dataset.keep)}`,
      { method: "POST" });
    route();
  });
};

/* ------------------------------------------------------------------ books */
let txFilter = "all";
ROUTES.books = async () => {
  const b = await api("/books");
  const shown = b.trades.filter(t =>
    txFilter === "all" || (txFilter === "settled" ? t.settled : !t.settled));
  $("#view").innerHTML = `
    <div class="head"><div><h1>Books</h1>
      <p>${esc(b.basis)} — cost basis against realised proceeds.</p></div></div>
    <div class="warnbox">Settled and estimated totals are shown apart and never added.
      One is measured against settlement data, the other computed from fee rates nobody
      has checked; a single total would be neither.</div>
    <div class="grid g2">
      <div class="card metric"><span class="l">Settled net</span>
        <span class="v measured">${money(b.settled_net_pence)}</span>
        <span class="n">${b.settled_count} trades from settlement data</span></div>
      <div class="card metric"><span class="l">Estimated net</span>
        <span class="v assumed">${money(b.estimated_net_pence)}</span>
        <span class="n">${b.estimated_count} trades on provisional fees</span></div>
    </div>
    <div class="card"><h2>Transactions</h2>
      <div class="chips" style="margin-bottom:.9rem">
        ${["all","settled","estimated"].map(f =>
          `<button class="chip ${txFilter===f?"on":""}" data-tx="${f}">${f}</button>`).join("")}</div>
      ${shown.length ? `<table><thead><tr><th>Item</th><th class="n">Cost</th>
        <th class="n">Gross</th><th class="n">Fees</th><th class="n">Net</th>
        <th class="n">ROI</th><th class="n">Held</th><th>Basis</th></tr></thead><tbody>
        ${shown.map(t => `<tr><td>${t.inventory_id}</td>
          <td class="n">${money(t.cost_pence)}</td><td class="n">${money(t.gross_pence)}</td>
          <td class="n">${money(t.fees_pence)}</td>
          <td class="n" style="color:${t.net_pence>=0?"var(--measured)":"var(--danger)"}">${money(t.net_pence)}</td>
          <td class="n">${pct(t.roi)}</td><td class="n">${t.days_held ?? "—"}d</td>
          <td>${t.settled ? badge("ok","settled") : badge("warn","estimated")}</td></tr>`).join("")}
        </tbody></table>` : `<div class="empty">No completed sales yet.</div>`}
    </div>`;
  document.querySelectorAll("[data-tx]").forEach(b2 =>
    b2.onclick = () => { txFilter = b2.dataset.tx; route(); });
};

/* ------------------------------------------------------------------ tax */
ROUTES.tax = async () => {
  const t = await api("/tax");
  const better = t.lower_method;
  $("#view").innerHTML = `
    <div class="head"><div><h1>Tax — ${esc(t.label)}</h1>
      <p>6 Apr ${esc(t.starts.slice(0,4))} to 5 Apr ${esc(t.ends.slice(0,4))}. Cash basis:
      income counts when received, costs when paid.</p></div></div>
    <div class="warnbox"><b>No liability figure, deliberately.</b> ${esc(t.disclaimer)}</div>
    ${t.figures_are_provisional ? `<div class="warnbox">Some sales are costed from the
      provisional fee table. These are not tax figures until <code>arb reconcile-fees</code>
      has run.</div>` : ""}
    <div class="grid g2">
      <div class="card metric"><span class="l">Gross income</span>
        <span class="v">${money(t.gross_income_pence)}</span>
        <span class="n">${t.sales_count} sales. The allowance is tested against this,
        before any costs — the part most often got wrong.</span></div>
      <div class="card metric"><span class="l">Allowable costs</span>
        <span class="v">${money(t.allowable_costs_pence)}</span>
        <span class="n">stock, fees and postage paid this year</span></div>
    </div>
    <div class="card"><h2>Two methods — you may use one, never both</h2>
      <div class="grid g2">
        <div class="card metric" style="border-color:${better==="actual_expenses"?"rgba(45,212,191,.4)":"var(--border)"}">
          <span class="l">Deduct actual expenses ${better==="actual_expenses"?badge("ok","lower"):""}</span>
          <span class="v">${money(t.profit_actual_expenses_pence)}</span>
          <span class="n">can create a loss</span></div>
        <div class="card metric" style="border-color:${better==="trading_allowance"?"rgba(45,212,191,.4)":"var(--border)"}">
          <span class="l">£1,000 trading allowance ${better==="trading_allowance"?badge("ok","lower"):""}</span>
          <span class="v">${money(t.profit_trading_allowance_pence)}</span>
          <span class="n">cannot create a loss</span></div>
      </div>
      <p class="note">Which is lower is arithmetic and is shown. Whether to claim it is
      not — the asymmetry above is one reason.</p></div>
    <div class="card"><h2>Registration</h2>
      <p>${t.below_threshold
        ? `Gross is at or under £1,000. Full relief normally applies and this income
           alone does not usually require registration.`
        : `Gross is over £1,000. If not already registered for Self Assessment, the
           deadline is <b>${esc(t.register_by)}</b>.`}</p>
      ${t.straddling_count ? `<p class="note">${t.straddling_count} trades straddle a year
        boundary — cost in one year, income in the next. Correct under cash basis,
        different under accruals.</p>` : ""}</div>`;
};

/* ------------------------------------------------------------------ reconcile */
ROUTES.reconcile = async () => {
  const r = await api("/reconcile/preview");
  $("#view").innerHTML = `
    <div class="head"><div><h1>Fee reconciliation</h1>
      <p>Predicted fee structure against what eBay actually charges.</p></div>
      ${badge(r.provisional ? "warn" : "ok", r.provisional ? "provisional" : "measured")}</div>
    <div class="warnbox"><b>Not a one-click operation.</b> Writing a corrected table
      changes its content hash, which bumps <code>fee_table_version</code>, which makes
      every score computed under the old version non-comparable until it is re-scored.
      Run <code>arb reconcile-fees --transactions &lt;file&gt;</code> to preview, then
      <code>--write</code> once you have read the drift.</div>
    <div class="card"><h2>Active table — ${esc(r.fee_table_version)}</h2>
      <table><thead><tr><th>Component</th><th>Kind</th><th>Scope</th>
        <th class="n">Assumed</th></tr></thead><tbody>
        ${r.components.map(c => `<tr><td>${esc(c.name.replace(/_/g," "))}</td>
          <td>${esc(c.kind)}</td><td>${esc(c.scope)}</td>
          <td class="n assumed">${c.rate != null ? (parseFloat(c.rate)*100).toFixed(2)+"%" : money(c.amount_pence)}</td>
        </tr>`).join("")}</tbody></table>
      <p class="note">${esc(r.note)}</p></div>`;
};

/* ------------------------------------------------------------------ labels */
ROUTES.labels = async () => {
  $("#view").innerHTML = `
    <div class="head"><div><h1>Label studio</h1>
      <p>Crop carrier labels to 6×4 and merge them into one printable batch.</p></div></div>
    <div class="warnbox">An unrecognised carrier passes through <b>uncropped</b> rather
      than cropped to a guessed region. That prints badly and visibly; a wrong crop
      prints beautifully and fails at the counter after the parcel is packed.</div>
    <div class="card"><div class="drop" id="drop">
      <div style="font-size:1.6rem;margin-bottom:.4rem">▤</div>
      <div>Drop carrier label PDFs here</div>
      <p class="note">Processing runs locally via <code>arb labels &lt;dir&gt;</code>.
      Files are not uploaded anywhere.</p></div>
      <div id="picked"></div></div>`;
  const drop = $("#drop");
  ["dragenter","dragover"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.add("over");
  }));
  ["dragleave","drop"].forEach(ev => drop.addEventListener(ev, e => {
    e.preventDefault(); drop.classList.remove("over");
  }));
  drop.addEventListener("drop", e => {
    const names = [...(e.dataTransfer?.files ?? [])].map(f => f.name).filter(n => n.endsWith(".pdf"));
    $("#picked").innerHTML = names.length
      ? `<div class="note" style="margin-top:1rem">${names.length} PDF(s) selected:
         ${names.map(esc).join(", ")}<br>Run <code>arb labels &lt;directory&gt; --out batch.pdf</code>
         to crop and merge them.</div>`
      : `<div class="note" style="margin-top:1rem">No PDFs in that drop.</div>`;
  });
};

/* ------------------------------------------------------------------ provenance */
ROUTES.provenance = async () => {
  const rows = await api("/provenance");
  const cards = rows.map(p => {
    const cls = p.status === "closed" ? "ok" : p.status === "unknown" ? "mute" : "warn";
    return `<div class="card">
      <div style="display:flex;justify-content:space-between;gap:.6rem;align-items:flex-start">
        <div><b>${esc(p.id)} · ${esc(p.gap)}</b></div>${badge(cls, p.status)}</div>
      <p class="note" style="margin-top:.5rem"><b>Now:</b> ${esc(p.standing_in)}</p>
      <p class="note"><b>Closed by:</b> ${esc(p.closed_by)}</p>
      <p class="note"><b>If wrong:</b> ${esc(p.blast_radius)}</p>
      <p class="note" style="color:var(--muted)"><b>Evidence:</b> ${esc(p.evidence)}</p>
    </div>`;
  }).join("");
  const open = rows.filter(r => r.status === "open").length;
  $("#view").innerHTML = `
    <div class="head"><div><h1>Provenance</h1>
      <p>Which numbers are still assumptions. Closed only on positive evidence —
      "nothing to check" reports unknown rather than passing.</p></div>
      ${badge(open ? "warn" : "ok", `${open} of ${rows.length} open`)}</div>
    <div class="grid g2">${cards}</div>`;
};

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);
