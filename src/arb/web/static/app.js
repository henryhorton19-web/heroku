/* ══════════════════════════════════════════════════════════════
   Arbitrage Trading Console — single-page frontend

   Reads the REST surface in src/arb/web/api.py. Deliberately thin:
   no client-side re-ranking, no client-side money maths. The server
   computed the ranking and the margins; this renders them.
   ══════════════════════════════════════════════════════════════ */

const API = '/api/v1';

/* ── plumbing ───────────────────────────────────────────────── */

async function get(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(`${res.status} ${await res.text().catch(() => '')}`.trim());
  return res.json();
}

async function post(path, body) {
  const res = await fetch(API + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) throw new Error(data.detail || `${res.status}`);
  return data;
}

const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};

const esc = (s) =>
  String(s ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const gbp = (pence) => {
  const n = Number(pence || 0) / 100;
  const s = Math.abs(n).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (n < 0 ? '\u2212\u00a3' : '\u00a3') + s;
};

const pct = (x, dp = 1) => (x === null || x === undefined ? '\u2014' : `${(x * 100).toFixed(dp)}%`);

function toast(msg, kind = 'ok') {
  const t = el('div', `toast ${kind}`, esc(msg));
  document.getElementById('toasts').appendChild(t);
  setTimeout(() => {
    t.style.transition = 'opacity .3s, transform .3s';
    t.style.opacity = '0';
    t.style.transform = 'translateX(24px)';
    setTimeout(() => t.remove(), 300);
  }, 4200);
}

function section(title, hint) {
  const s = el('div', 'section');
  s.appendChild(el('h2', null, esc(title)));
  if (hint) s.appendChild(el('span', 'hint', esc(hint)));
  s.appendChild(el('div', 'rule'));
  return s;
}

function empty(msg, sub) {
  return el('div', 'empty', `<div class="big">\u2014</div><div>${esc(msg)}</div>${
    sub ? `<div style="margin-top:.4rem;font-size:.75rem;opacity:.7">${esc(sub)}</div>` : ''}`);
}

function skeletons(n = 4) {
  const g = el('div', 'grid k4');
  for (let i = 0; i < n; i++) g.appendChild(el('div', 'skeleton'));
  return g;
}

function table(headers, rows) {
  const wrap = el('div', 'tablewrap');
  const scroll = el('div', 'tablescroll');
  const t = el('table');
  const thead = el('thead');
  const tr = el('tr');
  headers.forEach((h) => {
    const th = el('th', h.num ? 'num' : null, esc(h.label ?? h));
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  t.appendChild(thead);
  const tbody = el('tbody');
  rows.forEach((r) => tbody.appendChild(r));
  t.appendChild(tbody);
  scroll.appendChild(t);
  wrap.appendChild(scroll);
  return wrap;
}

function meter(x) {
  if (x === null || x === undefined) return '\u2014';
  return `<div style="display:flex;align-items:center;gap:.45rem">
    <div class="meter"><i style="transform:scaleX(${Math.max(0, Math.min(1, x))})"></i></div>
    <span class="mono" style="font-size:.72rem;color:var(--ink-dim)">${(x * 100).toFixed(0)}</span>
  </div>`;
}

/* ripple micro-animation on every button */
document.addEventListener('click', (e) => {
  const btn = e.target.closest('.btn');
  if (!btn || btn.disabled) return;
  const r = btn.getBoundingClientRect();
  const d = Math.max(r.width, r.height);
  const s = el('span', 'ripple');
  s.style.width = s.style.height = `${d}px`;
  s.style.left = `${e.clientX - r.left - d / 2}px`;
  s.style.top = `${e.clientY - r.top - d / 2}px`;
  btn.appendChild(s);
  setTimeout(() => s.remove(), 560);
});

/* ── Tab 1 · Executive Dashboard ────────────────────────────── */

async function renderDashboard(root) {
  root.appendChild(skeletons(6));
  const d = await get('/dashboard');
  root.innerHTML = '';

  if (d.synthetic_trades > 0 || d.open_placeholders > 0) {
    const bits = [];
    if (d.synthetic_trades > 0) bits.push(`${d.synthetic_trades} synthetic trade(s) are included in these figures`);
    if (d.open_placeholders > 0) bits.push(`${d.open_placeholders} open placeholder(s) in the provenance register`);
    root.appendChild(el('div', 'notice',
      `<span>\u26a0</span><span>${esc(bits.join('. '))}. Treat the headline numbers as provisional until both are zero.</span>`));
  }

  root.appendChild(section('Capital position', 'green = measured from settlement data, amber = estimated'));
  const grid = el('div', 'grid k3');
  d.metrics.forEach((m) => {
    const cls = m.measured === true ? 'measured' : m.measured === false ? 'estimated' : '';
    const card = el('div', `card metric ${cls}`);
    card.innerHTML = `
      <div class="label">${esc(m.label)}</div>
      <div class="value">${esc(m.value)}</div>
      ${m.note ? `<div class="note">${esc(m.note)}</div>` : ''}`;
    grid.appendChild(card);
  });
  root.appendChild(grid);

  root.appendChild(section('Lifecycle pipeline', 'count and cost basis at each stage'));
  const pipe = el('div', 'pipeline');
  d.pipeline.forEach((s) => {
    const st = el('div', `stage${s.derived ? ' derived' : ''}`);
    st.innerHTML = `
      <div class="k">${esc(s.label)}</div>
      <div class="n">${s.count}</div>
      <div class="c">${s.cost_pence ? gbp(s.cost_pence) : '\u2014'}</div>`;
    pipe.appendChild(st);
  });
  root.appendChild(pipe);

  if (d.verticals && d.verticals.length) {
    root.appendChild(section('By vertical'));
    const keys = Object.keys(d.verticals[0]);
    root.appendChild(table(
      keys.map((k) => ({ label: k.replace(/_/g, ' '), num: typeof d.verticals[0][k] === 'number' })),
      d.verticals.map((v) => {
        const tr = el('tr');
        keys.forEach((k) => {
          const val = v[k];
          const isNum = typeof val === 'number';
          const shown = k.endsWith('_pence') ? gbp(val) : isNum ? val.toLocaleString('en-GB') : esc(val ?? '\u2014');
          tr.appendChild(el('td', isNum ? 'num' : null, shown));
        });
        return tr;
      })));
  }
}

/* ── Tab 2 · Sourcing Workbench ─────────────────────────────── */

const workbench = { minNet: 0, limit: 50, reasons: [] };

async function renderSourcing(root) {
  root.appendChild(skeletons(3));

  if (!workbench.reasons.length) {
    workbench.reasons = await get('/skip-reasons').catch(() => []);
  }
  const rows = await get(`/opportunities?limit=${workbench.limit}&min_net_pence=${workbench.minNet}`);
  root.innerHTML = '';

  root.appendChild(el('div', 'notice info',
    '<span>\u24d8</span><span>Ordered by capital velocity as computed server-side. The workbench does not re-sort \u2014 the ranking shown is the ranking the buy side produced. A skip needs a reason; a buy needs a spend.</span>'));

  const bar = el('div', 'toolbar');
  bar.innerHTML = `
    <label class="field">Minimum net (\u00a3)
      <input type="number" id="wb-net" min="0" step="0.50" value="${(workbench.minNet / 100).toFixed(2)}" style="width:110px">
    </label>
    <label class="field">Rows
      <select id="wb-limit" style="width:90px">
        ${[25, 50, 100, 200].map((n) => `<option ${n === workbench.limit ? 'selected' : ''}>${n}</option>`).join('')}
      </select>
    </label>
    <button class="btn primary" id="wb-apply">Apply</button>
    <div style="flex:1"></div>
    <span class="badge accent">${rows.length} candidate${rows.length === 1 ? '' : 's'}</span>`;
  root.appendChild(bar);

  bar.querySelector('#wb-apply').onclick = () => {
    workbench.minNet = Math.round(parseFloat(bar.querySelector('#wb-net').value || '0') * 100);
    workbench.limit = parseInt(bar.querySelector('#wb-limit').value, 10);
    load('sourcing');
  };

  if (!rows.length) {
    root.appendChild(empty('No candidates above the threshold', 'Run a scan, or lower the minimum net.'));
    return;
  }

  const body = rows.map((o) => {
    const tr = el('tr');
    tr.innerHTML = `
      <td>
        <div class="t-title">${o.url ? `<a href="${esc(o.url)}" target="_blank" rel="noopener" style="color:inherit">${esc(o.title || 'untitled')}</a>` : esc(o.title || 'untitled')}</div>
        <div class="t-sub">${esc(o.brand || '\u2014')}${o.size ? ' \u00b7 ' + esc(o.size) : ''} \u00b7 ${esc(o.venue)}</div>
      </td>
      <td class="num">${gbp(o.price_pence)}</td>
      <td class="num ${o.net_pence >= 0 ? 'pos' : 'neg'}">${gbp(o.net_pence)}</td>
      <td class="num">${pct(o.roi)}</td>
      <td class="num">${o.capital_velocity === null || o.capital_velocity === undefined ? '\u2014' : o.capital_velocity.toFixed(2)}</td>
      <td>${meter(o.est_confidence)}</td>
      <td>${meter(o.match_confidence)}</td>
      <td class="num">${o.comp_n ?? '\u2014'}</td>`;

    const act = el('td');
    const row = el('div', 'btnrow');

    const buy = el('button', 'btn buy', 'Buy');
    buy.onclick = async () => {
      const spend = prompt(`Spend actually paid, in pounds (listing shows ${gbp(o.price_pence)}):`,
        (o.price_pence / 100).toFixed(2));
      if (spend === null) return;
      try {
        await post('/decisions', { opportunity_id: o.id, outcome: 'bought', spend: spend.trim() });
        toast('Purchase recorded');
        tr.style.opacity = '0.35';
        row.querySelectorAll('button').forEach((b) => (b.disabled = true));
      } catch (e) { toast(e.message, 'bad'); }
    };

    const sel = el('select');
    sel.innerHTML = `<option value="">skip reason\u2026</option>` +
      workbench.reasons.map((r) => `<option value="${esc(r)}">${esc(r.replace(/_/g, ' '))}</option>`).join('');

    const skip = el('button', 'btn skip', 'Skip');
    skip.onclick = async () => {
      if (!sel.value) { toast('A skip needs a reason \u2014 AutoBuy scores itself against these', 'bad'); sel.focus(); return; }
      try {
        await post('/decisions', { opportunity_id: o.id, outcome: 'skipped', skip_reason: sel.value });
        toast('Skip recorded');
        tr.style.opacity = '0.35';
        row.querySelectorAll('button').forEach((b) => (b.disabled = true));
      } catch (e) { toast(e.message, 'bad'); }
    };

    row.append(buy, sel, skip);
    act.appendChild(row);
    tr.appendChild(act);
    return tr;
  });

  root.appendChild(table([
    'Listing',
    { label: 'Ask', num: true },
    { label: 'Net', num: true },
    { label: 'ROI', num: true },
    { label: 'Velocity', num: true },
    'Est. conf',
    'Match conf',
    { label: 'Comps', num: true },
    'Decision',
  ], body));
}

/* ── Tab 3 · Inventory & Hazards ────────────────────────────── */

async function renderInventory(root) {
  root.appendChild(skeletons(3));
  const [inv, haz, own] = await Promise.all([
    get('/inventory'),
    get('/hazards'),
    get('/own-listings').catch(() => []),
  ]);
  root.innerHTML = '';

  const hazards = haz.hazards || [];

  root.appendChild(section('Double-sale hazards', 'items live on more than one venue'));
  const summary = el('div', 'grid k4');
  summary.innerHTML = `
    <div class="card metric ${hazards.length ? 'estimated' : 'measured'}">
      <div class="label">Open hazards</div><div class="value">${hazards.length}</div>
      <div class="note">at risk of selling twice</div></div>
    <div class="card metric"><div class="label">Delists in flight</div>
      <div class="value">${haz.in_flight ?? 0}</div>
      <div class="note">requested, awaiting venue confirmation</div></div>
    <div class="card metric"><div class="label">Own listings</div>
      <div class="value">${own.length}</div><div class="note">across all venues</div></div>
    <div class="card metric"><div class="label">Units held</div>
      <div class="value">${inv.length}</div>
      <div class="note">${gbp(inv.reduce((a, r) => a + (r.cost_pence || 0), 0))} cost basis</div></div>`;
  root.appendChild(summary);

  if (hazards.length) {
    const venues = [...new Set(own.map((o) => o.venue))];
    root.appendChild(table(
      ['Inventory', 'Venue', 'External ID', 'Kind', 'Detail', 'Action'],
      hazards.map((h) => {
        const tr = el('tr');
        tr.innerHTML = `
          <td class="mono">#${h.inventory_id}</td>
          <td><span class="badge neutral">${esc(h.venue)}</span></td>
          <td class="mono" style="font-size:.72rem">${esc(h.external_id || '\u2014')}</td>
          <td><span class="badge warn"><span class="dot pulse"></span>${esc(h.kind)}</span></td>
          <td style="font-size:.76rem;color:var(--ink-dim)">${esc(h.detail || '\u2014')}</td>`;
        const td = el('td');
        const row = el('div', 'btnrow');
        (venues.length ? venues : [h.venue]).forEach((v) => {
          const b = el('button', 'btn', `Keep ${esc(v)}`);
          b.onclick = async () => {
            try {
              const r = await post(`/hazards/${h.inventory_id}/request-delist?keep_venue=${encodeURIComponent(v)}`);
              toast(`Delist requested on ${(r.requested || []).join(', ') || 'nothing'} \u2014 pending venue confirmation`);
              load('inventory');
            } catch (e) { toast(e.message, 'bad'); }
          };
          row.appendChild(b);
        });
        td.appendChild(row);
        tr.appendChild(td);
        return tr;
      })));
    root.appendChild(el('div', 'notice',
      '<span>\u26a0</span><span>Delist is <strong>requested, not performed</strong>. The row moves to <code>delist_pending</code> and only the venue\u2019s own answer resolves it.</span>'));
  } else {
    root.appendChild(el('div', 'tablewrap')).appendChild(empty('No double-sale hazards', 'Every held item is live on at most one venue.'));
  }

  root.appendChild(section('Cross-venue listings'));
  if (!own.length) {
    root.appendChild(el('div', 'tablewrap')).appendChild(empty('Nothing listed'));
  } else {
    root.appendChild(table(
      ['Inventory', 'Venue', 'External ID', { label: 'Ask', num: true }, 'Status'],
      own.map((o) => {
        const tr = el('tr');
        let status = '<span class="badge info"><span class="dot pulse"></span>live</span>';
        if (o.delist_error) status = `<span class="badge bad"><span class="dot"></span>delist failed</span>`;
        else if (o.delisted_at) status = '<span class="badge neutral"><span class="dot"></span>delisted</span>';
        else if (o.delist_requested_at) status = '<span class="badge warn"><span class="dot pulse"></span>delist pending</span>';
        else if (o.sold_at) status = '<span class="badge ok"><span class="dot"></span>sold</span>';
        tr.innerHTML = `
          <td class="mono">#${o.inventory_id}</td>
          <td><span class="badge neutral">${esc(o.venue)}</span></td>
          <td class="mono" style="font-size:.72rem">${esc(o.external_id || '\u2014')}</td>
          <td class="num">${gbp(o.ask_pence)}</td>
          <td>${status}${o.delist_error ? `<div class="t-sub">${esc(o.delist_error)}</div>` : ''}</td>`;
        return tr;
      })));
  }

  root.appendChild(section('Held stock', 'newest first'));
  if (!inv.length) {
    root.appendChild(el('div', 'tablewrap')).appendChild(empty('No inventory'));
  } else {
    root.appendChild(table(
      ['ID', 'State', { label: 'Cost', num: true }, { label: 'Gross', num: true },
        { label: 'Age', num: true }, 'Acquired', 'Flags'],
      inv.map((r) => {
        const tr = el('tr');
        const aged = r.age_days > 60 && !r.sold_at;
        tr.innerHTML = `
          <td class="mono">#${r.id}</td>
          <td><span class="badge ${r.sold_at ? 'ok' : 'accent'}">${esc(String(r.state).replace(/_/g, ' '))}</span></td>
          <td class="num">${gbp(r.cost_pence)}</td>
          <td class="num">${r.gross_pence ? gbp(r.gross_pence) : '\u2014'}</td>
          <td class="num" style="${aged ? 'color:var(--amber)' : ''}">${r.age_days}d</td>
          <td class="mono" style="font-size:.72rem;color:var(--ink-faint)">${esc(String(r.acquired_at).slice(0, 10))}</td>
          <td>${r.settled ? '<span class="badge ok">settled</span>' : '<span class="badge warn">provisional fees</span>'}
              ${r.synthetic ? ' <span class="badge bad">synthetic</span>' : ''}</td>`;
        return tr;
      })));
  }
}

/* ── Tab 4 · Ledger, Fees & Tax ─────────────────────────────── */

async function renderLedger(root) {
  root.appendChild(skeletons(4));
  const [books, tax, fees] = await Promise.all([
    get('/books'),
    get('/tax'),
    get('/reconcile/preview'),
  ]);
  root.innerHTML = '';

  root.appendChild(section('Realised position', esc(books.basis)));
  const g = el('div', 'grid k4');
  g.innerHTML = `
    <div class="card metric measured"><div class="label">Net \u2014 settled</div>
      <div class="value">${gbp(books.settled_net_pence)}</div>
      <div class="note">${books.settled_count} trade(s) on real settlement data</div></div>
    <div class="card metric estimated"><div class="label">Net \u2014 estimated</div>
      <div class="value">${gbp(books.estimated_net_pence)}</div>
      <div class="note">${books.estimated_count} trade(s) on provisional fees</div></div>
    <div class="card metric"><div class="label">Fee table</div>
      <div class="value" style="font-size:1.05rem">${esc(fees.fee_table_version)}</div>
      <div class="note">${fees.provisional ? 'provisional \u2014 not reconciled against settlement' : 'reconciled'}</div></div>
    <div class="card metric"><div class="label">Trades booked</div>
      <div class="value">${(books.trades || []).length}</div>
      <div class="note">${books.never_summed ? 'settled and estimated shown apart, never summed' : ''}</div></div>`;
  root.appendChild(g);

  if (books.never_summed) {
    root.appendChild(el('div', 'notice info',
      '<span>\u24d8</span><span>Settled and estimated nets are shown separately and are <strong>never summed</strong>. Adding a measured figure to a modelled one produces a number with no defined meaning.</span>'));
  }

  root.appendChild(section('Trade ledger'));
  const trades = books.trades || [];
  if (!trades.length) {
    root.appendChild(el('div', 'tablewrap')).appendChild(empty('No closed trades yet'));
  } else {
    root.appendChild(table(
      ['ID', { label: 'Cost', num: true }, { label: 'Gross', num: true }, { label: 'Fees', num: true },
        { label: 'Ship', num: true }, { label: 'Net', num: true }, { label: 'ROI', num: true },
        { label: 'Held', num: true }, 'Basis'],
      trades.map((t) => {
        const tr = el('tr');
        tr.innerHTML = `
          <td class="mono">#${t.inventory_id}</td>
          <td class="num">${gbp(t.cost_pence)}</td>
          <td class="num">${gbp(t.gross_pence)}</td>
          <td class="num neg">${gbp(t.fees_pence)}</td>
          <td class="num neg">${gbp(t.ship_pence)}</td>
          <td class="num ${t.net_pence >= 0 ? 'pos' : 'neg'}" style="font-weight:600">${gbp(t.net_pence)}</td>
          <td class="num">${pct(t.roi)}</td>
          <td class="num">${t.days_held ?? '\u2014'}d</td>
          <td>${t.settled ? '<span class="badge ok">settled</span>' : '<span class="badge warn">estimated</span>'}</td>`;
        return tr;
      })));
  }

  root.appendChild(section('Fee reconciler', `table ${fees.fee_table_version}`));
  const fc = el('div', 'card');
  fc.appendChild(el('div', null, (fees.components || []).map((c) => `
    <div class="kv"><span class="k">${esc(c.name)} <span style="opacity:.6">\u00b7 ${esc(c.kind)} \u00b7 ${esc(c.scope)}</span></span>
    <span class="v">${c.rate !== null ? esc(c.rate) : ''}${c.amount_pence !== null ? ' ' + gbp(c.amount_pence) : ''}</span></div>`).join('')));
  fc.appendChild(el('div', 'notice', `<span>\u26a0</span><span>${esc(fees.note)} Writing requires explicit confirmation and invalidates every score computed under the previous version.</span>`));
  root.appendChild(fc);

  root.appendChild(section(`UK tax \u2014 ${esc(tax.label)}`, `${tax.starts} to ${tax.ends}`));
  const tg = el('div', 'grid k4');
  tg.innerHTML = `
    <div class="card metric"><div class="label">Gross income</div><div class="value">${gbp(tax.gross_income_pence)}</div>
      <div class="note">${tax.sales_count} sale(s)</div></div>
    <div class="card metric"><div class="label">Allowable costs</div><div class="value">${gbp(tax.allowable_costs_pence)}</div></div>
    <div class="card metric ${tax.lower_method === 'actual_expenses' ? 'measured' : ''}">
      <div class="label">Profit \u2014 actual expenses</div><div class="value">${gbp(tax.profit_actual_expenses_pence)}</div></div>
    <div class="card metric ${tax.lower_method === 'trading_allowance' ? 'measured' : ''}">
      <div class="label">Profit \u2014 trading allowance</div><div class="value">${gbp(tax.profit_trading_allowance_pence)}</div>
      <div class="note">allowance ${gbp(tax.trading_allowance_pence)}</div></div>`;
  root.appendChild(tg);

  const tn = el('div', 'card');
  tn.style.marginTop = '.9rem';
  tn.innerHTML = `
    <div class="kv"><span class="k">Lower method</span><span class="v">${esc(String(tax.lower_method).replace(/_/g, ' '))}</span></div>
    <div class="kv"><span class="k">Below reporting threshold</span><span class="v">${tax.below_threshold ? 'yes' : 'no'}</span></div>
    <div class="kv"><span class="k">Register by</span><span class="v">${esc(tax.register_by)}</span></div>
    <div class="kv"><span class="k">Straddling sales</span><span class="v">${tax.straddling_count}</span></div>
    <div class="kv"><span class="k">Figures provisional</span><span class="v">${tax.figures_are_provisional ? 'yes' : 'no'}</span></div>`;
  root.appendChild(tn);
  root.appendChild(el('div', 'notice', `<span>\u26a0</span><span>${esc(tax.disclaimer)}</span>`));
}

/* ── Tab 5 · System Telemetry ───────────────────────────────── */

const MONITORS = ['scanner', 'comps', 'reprice', 'sweep', 'crossvenue'];

async function renderSystem(root) {
  root.appendChild(skeletons(4));
  const [health, prov, fees] = await Promise.all([
    get('/health'),
    get('/provenance').catch(() => []),
    get('/reconcile/preview').catch(() => ({})),
  ]);
  const monitors = await Promise.all(
    MONITORS.map((m) => get(`/monitors/${m}/health`).then((r) => ({ ...r, found: true })).catch(() => null)),
  );
  root.innerHTML = '';

  root.appendChild(section('Runtime', 'the process, the database, the fee table'));
  const g = el('div', 'grid k4');
  g.innerHTML = `
    <div class="card metric ${health.ok ? 'measured' : 'estimated'}">
      <div class="label">API</div>
      <div class="value" style="font-size:1.15rem">${health.ok ? 'healthy' : 'degraded'}</div>
      <div class="note">FastAPI \u00b7 local single-process</div></div>
    <div class="card metric"><div class="label">Database</div>
      <div class="value" style="font-size:.95rem;word-break:break-all">SQLite</div>
      <div class="note mono" style="font-size:.68rem">${esc(health.db)}</div></div>
    <div class="card metric"><div class="label">Comp freshness window</div>
      <div class="value">${health.freshness_days}d</div>
      <div class="note">comps older than this are not trusted</div></div>
    <div class="card metric ${fees.provisional ? 'estimated' : 'measured'}">
      <div class="label">Fee table</div>
      <div class="value" style="font-size:1.05rem">${esc(fees.fee_table_version || '\u2014')}</div>
      <div class="note">${fees.provisional ? 'provisional' : 'reconciled against settlement'}</div></div>`;
  root.appendChild(g);

  root.appendChild(section('Monitor health', 'staleness is a first-class failure, not an absence of alerts'));
  const live = monitors.filter(Boolean);
  if (!live.length) {
    root.appendChild(el('div', 'tablewrap')).appendChild(
      empty('No monitor runs recorded', 'A monitor with no history is not the same as a monitor that is healthy.'));
  } else {
    root.appendChild(table(
      ['Monitor', 'Last status', 'Last success', { label: 'Consecutive failures', num: true }, 'Freshness'],
      live.map((m) => {
        const tr = el('tr');
        const statusCls = m.last_status === 'ok' ? 'ok' : m.last_status ? 'bad' : 'neutral';
        tr.innerHTML = `
          <td class="t-title">${esc(m.monitor)}</td>
          <td><span class="badge ${statusCls}"><span class="dot ${statusCls === 'ok' ? 'pulse' : ''}"></span>${esc(m.last_status || 'never run')}</span></td>
          <td class="mono" style="font-size:.72rem;color:var(--ink-faint)">${esc(m.last_success ? String(m.last_success).replace('T', ' ').slice(0, 16) : '\u2014')}</td>
          <td class="num" style="${m.consecutive_failures > 0 ? 'color:var(--red)' : ''}">${m.consecutive_failures}</td>
          <td>${m.stale ? '<span class="badge warn"><span class="dot pulse"></span>stale</span>' : '<span class="badge ok"><span class="dot"></span>fresh</span>'}</td>`;
        return tr;
      })));
  }

  root.appendChild(section('Venue connectivity', 'venues this build actually trades on'));
  const vg = el('div', 'grid k2');
  vg.innerHTML = `
    <div class="card">
      <div class="label" style="font-size:.7rem;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:.6rem">Vinted</div>
      <div class="btnrow"><span class="badge accent">BuyVenue</span><span class="badge accent">SellVenue</span></div>
      <div class="note" style="margin-top:.6rem;font-size:.73rem;color:var(--ink-faint)">Buy-side discovery and sell-side publishing.</div>
    </div>
    <div class="card">
      <div class="label" style="font-size:.7rem;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:.6rem">eBay</div>
      <div class="btnrow"><span class="badge accent">CompSource</span><span class="badge accent">SellVenue</span></div>
      <div class="note" style="margin-top:.6rem;font-size:.73rem;color:var(--ink-faint)">Sold comps for valuation, plus a second sell channel.</div>
    </div>`;
  root.appendChild(vg);

  root.appendChild(section('Provenance register', 'what is standing in for something that does not exist yet'));
  if (!prov.length) {
    root.appendChild(el('div', 'tablewrap')).appendChild(empty('Register empty'));
  } else {
    root.appendChild(table(
      ['Gap', 'Standing in', 'Closed by', 'Blast radius', 'Status'],
      prov.map((p) => {
        const tr = el('tr');
        const cls = p.status === 'open' ? 'warn' : p.status === 'closed' ? 'ok' : 'neutral';
        tr.innerHTML = `
          <td class="t-title">${esc(p.gap)}</td>
          <td style="font-size:.76rem;color:var(--ink-dim)">${esc(p.standing_in)}</td>
          <td style="font-size:.76rem;color:var(--ink-dim)">${esc(p.closed_by)}</td>
          <td><span class="badge neutral">${esc(p.blast_radius)}</span></td>
          <td><span class="badge ${cls}"><span class="dot"></span>${esc(p.status)}</span>${
            p.evidence ? `<div class="t-sub">${esc(p.evidence)}</div>` : ''}</td>`;
        return tr;
      })));
  }
}

/* ── router ─────────────────────────────────────────────────── */

const TABS = {
  dashboard: renderDashboard,
  sourcing: renderSourcing,
  inventory: renderInventory,
  ledger: renderLedger,
  system: renderSystem,
};

let current = 'dashboard';

async function load(tab) {
  current = tab;
  document.querySelectorAll('nav.tabs button').forEach((b) => {
    b.setAttribute('aria-selected', String(b.dataset.tab === tab));
  });
  if (location.hash.slice(2) !== tab) history.replaceState(null, '', `#/${tab}`);

  const view = document.getElementById('view');
  view.innerHTML = '';
  const root = el('div', 'panel-view');
  view.appendChild(root);

  try {
    await TABS[tab](root);
  } catch (e) {
    root.innerHTML = '';
    root.appendChild(el('div', 'notice',
      `<span>\u26a0</span><span><strong>Could not load this tab.</strong><br>${esc(e.message)}</span>`));
  }
}

async function pingHealth() {
  const db = document.getElementById('db-badge');
  const fee = document.getElementById('fee-badge');
  try {
    const h = await get('/health');
    db.className = 'badge ok';
    db.innerHTML = `<span class="dot pulse"></span>connected`;
    db.title = h.db;
    const f = await get('/reconcile/preview').catch(() => null);
    if (f) {
      fee.className = `badge ${f.provisional ? 'warn' : 'ok'}`;
      fee.textContent = `fee table ${f.fee_table_version}${f.provisional ? ' \u00b7 provisional' : ''}`;
    }
  } catch {
    db.className = 'badge bad';
    db.innerHTML = `<span class="dot"></span>offline`;
  }
}

document.querySelectorAll('nav.tabs button').forEach((b) => {
  b.onclick = () => load(b.dataset.tab);
});
document.getElementById('refresh').onclick = () => { load(current); pingHealth(); };
window.addEventListener('hashchange', () => {
  const t = location.hash.slice(2);
  if (TABS[t] && t !== current) load(t);
});

const initial = location.hash.slice(2);
load(TABS[initial] ? initial : 'dashboard');
pingHealth();
setInterval(pingHealth, 30000);
