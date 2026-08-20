const $ = (s, r = document) => r.querySelector(s);
const money = p => "£" + (p / 100).toFixed(2);
const pct = v => (v * 100).toFixed(1) + "%";
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

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

const badge = (kind, text) => `<span class="badge ${kind}">${esc(text)}</span>`;

// ──────────────────────────────────────────────────────────────
// Routing
// ──────────────────────────────────────────────────────────────

async function route() {
  shell();
  const key = (location.hash || "#/dashboard").slice(2).split("?")[0];
  const view = ROUTES[key] || ROUTES.dashboard;
  loading();
  try { await view(); } catch (e) { failed(e); }
}

const ROUTES = {
  dashboard: renderDashboard,
  engine: renderEngine,
  provenance: renderProvenance,
};

function shell() {
  const nav = document.getElementById("nav");
  if (!nav) return;
  nav.querySelectorAll("a").forEach(a => a.classList.remove("active"));
  const active = nav.querySelector(`a[href="#${location.hash || "#/dashboard"}"]`);
  if (active) active.classList.add("active");
}

function loading() {
  document.getElementById("main").innerHTML = `<p>Loading...</p>`;
}

function failed(e) {
  document.getElementById("main").innerHTML = `<p class="error">Error: ${esc(e.message)}</p>`;
}

// ──────────────────────────────────────────────────────────────
// Dashboard (stub)
// ──────────────────────────────────────────────────────────────

async function renderDashboard() {
  const main = document.getElementById("main");
  main.innerHTML = `
    <div class="card">
      <h2>Dashboard</h2>
      <p>Welcome to the Arbitrage Dashboard.</p>
    </div>
  `;
}

// ──────────────────────────────────────────────────────────────
// Engine Control Center
// ──────────────────────────────────────────────────────────────

async function renderEngine() {
  const main = document.getElementById("main");
  main.innerHTML = `<h2>Engine Control Center</h2><div class="engine-grid" id="engine-grid"></div>`;

  const grid = document.getElementById("engine-grid");

  // Fetch all engine statuses in parallel
  try {
    const [status, proxies, captcha, autocop, crosslister] = await Promise.all([
      api("/engine/status"),
      api("/engine/proxies"),
      api("/engine/captcha"),
      api("/engine/autocop"),
      api("/engine/crosslister"),
    ]);

    grid.innerHTML = `
      <div class="engine-card">
        <h3>Status</h3>
        <div class="stat"><span class="stat-label">Enabled:</span> <span class="stat-value">${status.enabled ? badge("green", "Active") : badge("red", "Disabled")}</span></div>
        <div class="stat"><span class="stat-label">TLS Preset:</span> <span class="stat-value">${esc(status.tls_preset)}</span></div>
        <div class="stat"><span class="stat-label">Polling Latency:</span> <span class="stat-value">${status.polling_latency_ms.toFixed(2)} ms</span></div>
        <div class="stat"><span class="stat-label">Request Rate:</span> <span class="stat-value">${status.request_rate.toFixed(2)} req/s</span></div>
        <button onclick="toggleEngine()">Toggle Engine</button>
      </div>

      <div class="engine-card">
        <h3>Proxies</h3>
        <div class="stat"><span class="stat-label">Total:</span> <span class="stat-value">${proxies.total}</span></div>
        <div class="stat"><span class="stat-label">Available:</span> <span class="stat-value">${proxies.available}</span></div>
        <div class="stat"><span class="stat-label">Quarantined:</span> <span class="stat-value">${proxies.quarantined.length}</span></div>
        <div><input type="text" id="quarantine-ip" placeholder="IP address" style="width:100%;margin-bottom:0.5rem;padding:0.3rem;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;"></div>
        <button onclick="quarantineIP()">Quarantine IP</button>
        <button onclick="unquarantineIP()" style="margin-left:0.5rem;">Unquarantine IP</button>
      </div>

      <div class="engine-card">
        <h3>CAPTCHA</h3>
        <div class="stat"><span class="stat-label">CapSolver:</span> <span class="stat-value">${captcha.capsolver_configured ? badge("green", "Configured") : badge("amber", "Not configured")}</span></div>
        <div class="stat"><span class="stat-label">2Captcha:</span> <span class="stat-value">${captcha.twocaptcha_configured ? badge("green", "Configured") : badge("amber", "Not configured")}</span></div>
        <div class="stat"><span class="stat-label">Solve Count:</span> <span class="stat-value">${captcha.solve_count}</span></div>
        <div class="stat"><span class="stat-label">Avg Duration:</span> <span class="stat-value">${captcha.average_duration_ms.toFixed(1)} ms</span></div>
        <div class="stat"><span class="stat-label">Success Rate:</span> <span class="stat-value">${pct(captcha.success_rate)}</span></div>
        <button onclick="testCaptcha()">Test CAPTCHA</button>
      </div>

      <div class="engine-card">
        <h3>AutoCop</h3>
        <div class="stat"><span class="stat-label">Armed:</span> <span class="stat-value">${autocop.armed ? badge("green", "Armed") : badge("amber", "Disarmed")}</span></div>
        <div class="stat"><span class="stat-label">Max Spend:</span> <span class="stat-value">${money(autocop.max_spend_pence)}</span></div>
        <div class="stat"><span class="stat-label">Dry Run:</span> <span class="stat-value">${autocop.dry_run ? badge("green", "Enabled") : badge("amber", "Disabled")}</span></div>
        <div class="stat"><span class="stat-label">Recent Purchases:</span> <span class="stat-value">${autocop.recent_purchases.length}</span></div>
        <div><input type="number" id="max-spend" value="${autocop.max_spend_pence / 100}" step="0.01" style="width:100%;margin-bottom:0.5rem;padding:0.3rem;background:#21262d;border:1px solid #30363d;color:#c9d1d9;border-radius:4px;"></div>
        <button onclick="configureAutoCop()">Update Max Spend</button>
      </div>

      <div class="engine-card">
        <h3>Cross-Lister</h3>
        <div class="stat"><span class="stat-label">Vinted:</span> <span class="stat-value">${crosslister.venues.vinted === "connected" ? badge("green", "Connected") : badge("red", "Disconnected")}</span></div>
        <div class="stat"><span class="stat-label">eBay:</span> <span class="stat-value">${crosslister.venues.ebay === "connected" ? badge("green", "Connected") : badge("red", "Disconnected")}</span></div>
        <div class="stat"><span class="stat-label">Depop:</span> <span class="stat-value">${crosslister.venues.depop === "connected" ? badge("green", "Connected") : badge("red", "Disconnected")}</span></div>
        <div class="stat"><span class="stat-label">Poshmark:</span> <span class="stat-value">${crosslister.venues.poshmark === "connected" ? badge("green", "Connected") : badge("red", "Disconnected")}</span></div>
        <div class="stat"><span class="stat-label">Mercari:</span> <span class="stat-value">${crosslister.venues.mercari === "connected" ? badge("green", "Connected") : badge("red", "Disconnected")}</span></div>
        <div class="stat"><span class="stat-label">Active Delist Queue:</span> <span class="stat-value">${crosslister.active_delist_queue}</span></div>
      </div>
    `;
  } catch (e) {
    grid.innerHTML = `<p class="error">Failed to load engine status: ${esc(e.message)}</p>`;
  }
}

// ──────────────────────────────────────────────────────────────
// Engine actions
// ──────────────────────────────────────────────────────────────

async function toggleEngine() {
  try {
    const status = await api("/engine/status");
    const newEnabled = !status.enabled;
    await api("/engine/toggle", {
      method: "POST",
      body: JSON.stringify({ enabled: newEnabled }),
    });
    renderEngine();
  } catch (e) {
    alert("Toggle failed: " + e.message);
  }
}

async function quarantineIP() {
  const ip = document.getElementById("quarantine-ip").value;
  if (!ip) return alert("Enter an IP address");
  try {
    await api("/engine/proxies/quarantine", {
      method: "POST",
      body: JSON.stringify({ ip }),
    });
    renderEngine();
  } catch (e) {
    alert("Quarantine failed: " + e.message);
  }
}

async function unquarantineIP() {
  const ip = document.getElementById("quarantine-ip").value;
  if (!ip) return alert("Enter an IP address");
  try {
    await api("/engine/proxies/unquarantine", {
      method: "POST",
      body: JSON.stringify({ ip }),
    });
    renderEngine();
  } catch (e) {
    alert("Unquarantine failed: " + e.message);
  }
}

async function testCaptcha() {
  try {
    const result = await api("/engine/captcha/test", { method: "POST" });
    alert("Test result: " + result.result);
  } catch (e) {
    alert("Test failed: " + e.message);
  }
}

async function configureAutoCop() {
  const maxSpendInput = document.getElementById("max-spend");
  const maxSpendPence = Math.round(parseFloat(maxSpendInput.value) * 100);
  if (isNaN(maxSpendPence)) return alert("Enter a valid amount");
  try {
    await api("/engine/autocop/config", {
      method: "POST",
      body: JSON.stringify({ max_spend_pence: maxSpendPence }),
    });
    renderEngine();
  } catch (e) {
    alert("Config failed: " + e.message);
  }
}

// ──────────────────────────────────────────────────────────────
// Provenance (stub)
// ──────────────────────────────────────────────────────────────

async function renderProvenance() {
  const main = document.getElementById("main");
  main.innerHTML = `
    <div class="card">
      <h2>Provenance</h2>
      <p>Placeholder register will be displayed here.</p>
    </div>
  `;
}

// ──────────────────────────────────────────────────────────────
// Init
// ──────────────────────────────────────────────────────────────

window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", route);
