/*
 * System dashboard — polls the auth-gated /api/system/health/full (the public
 * /api/system/health is a stripped probe payload) and renders the health grid.
 * Standalone page (does not load main.js); uses a tiny credentialed fetch.
 */
(function () {
  "use strict";

  const COLORS = { healthy: "#00ff88", warning: "#ffcc00", critical: "#ff4455" };
  function colorFor(status) { return COLORS[status] || "#88aaff"; }

  // Server strings here include raw exception text — escape everything.
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  async function apiGet(url) {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  function renderHealthDashboard(health) {
    // ── Overall + database ───────────────────────────────────────────────
    const statusColor = colorFor(health.status);
    const db = health.database || {};
    const dbColor = colorFor(db.status);
    document.getElementById("system-health-container").innerHTML = `
      <div style="font-size:24px;font-weight:700;color:${statusColor};">● ${esc(String(health.status).toUpperCase())}</div>
      <p style="font-size:12px;color:#88a;">Last checked: ${new Date(health.timestamp).toLocaleTimeString()}</p>
      <p style="color:${dbColor};">Database: ${esc(db.status || "?")}${db.error ? " — " + esc(db.error) : ""}</p>
      <p style="font-size:12px;color:#88a;">${db.agents_tracked != null ? esc(db.agents_tracked) + " agents tracked" : ""}</p>
    `;

    // ── Backups ──────────────────────────────────────────────────────────
    const bk = health.backups || {};
    document.getElementById("backup-status").innerHTML = `
      <p style="color:${colorFor(bk.status)};">${esc(bk.message || bk.status || "unknown")}</p>
      <p style="font-size:12px;color:#88a;">Last: ${esc(bk.last_backup || "Never")}</p>
    `;

    // ── Scheduled jobs ───────────────────────────────────────────────────
    const jobs = health.scheduled_jobs || {};
    const missing = (jobs.missing && jobs.missing.length)
      ? `<p style="font-size:12px;color:${COLORS.critical};">Missing: ${esc(jobs.missing.join(", "))}</p>` : "";
    document.getElementById("scheduled-jobs").innerHTML = `
      <p style="color:${colorFor(jobs.status)};">${Number(jobs.total_jobs) || 0} jobs scheduled</p>
      <p style="font-size:12px;color:#88a;">${esc(jobs.message || "")}</p>
      ${missing}
    `;

    // ── Critical agents ──────────────────────────────────────────────────
    const agents = health.agents || {};
    const lines = [];
    (agents.critical || []).forEach(a => lines.push(`<p style="color:${COLORS.critical};">❌ ${esc(a)}</p>`));
    (agents.warnings || []).forEach(a => lines.push(`<p style="color:${COLORS.warning};">⚠ ${esc(a)}</p>`));
    document.getElementById("critical-alerts").innerHTML =
      lines.length ? lines.join("") : `<p style="color:${COLORS.healthy};">✓ All agents healthy</p>`;

    // ── External integrations ────────────────────────────────────────────
    const apis = (health.external_apis && health.external_apis.apis) || {};
    const apisHtml = Object.entries(apis).map(([api, status]) => {
      const color = status === "configured" ? COLORS.healthy : COLORS.warning;
      return `<p style="color:${color};">${esc(api)}: ${esc(status)}</p>`;
    }).join("");
    document.getElementById("external-apis").innerHTML = apisHtml || "<p>No integrations checked</p>";
  }

  function loadSystemHealth() {
    apiGet("/api/system/health/full")
      .then(renderHealthDashboard)
      .catch(e => {
        document.getElementById("system-health-container").innerHTML =
          `<p style="color:${COLORS.critical};">Error: ${esc(e.message || e)}</p>`;
      })
      .finally(() => setTimeout(loadSystemHealth, 30000));
  }

  loadSystemHealth();
})();


/*
 * System clock — set a simulated "now" so logs (meals, workouts, cardio, water)
 * are backfilled to an earlier date/time until reset. POSTs to
 * /api/settings/simulated-time; the patched window.fetch (_csrf.html, included
 * via nav) adds the X-CSRF-Token header automatically.
 */
(function () {
  "use strict";

  const GREEN = "#00ff88", AMBER = "#ffcc00", DIM = "#88aaff";
  const container = document.getElementById("system-clock-container");
  if (!container) return;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  async function apiGet(url) {
    const r = await fetch(url, { credentials: "include" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }
  async function apiPost(url, body) {
    const r = await fetch(url, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }

  // Real wall clock, formatted in the app timezone (Europe/London) regardless of
  // the browser's own timezone. Ticks locally once a second.
  const REAL_FMT = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Europe/London", weekday: "short", hour: "2-digit",
    minute: "2-digit", second: "2-digit", hour12: true,
  });
  // Simulated instant: server sends an ISO string already offset to the app tz,
  // so format it without re-applying a timezone.
  const SIM_FMT = new Intl.DateTimeFormat("en-GB", {
    weekday: "long", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit", hour12: true,
  });

  let state = { is_simulated: false, simulated_time: null };
  let realTimer = null;

  // Value for <input type="datetime-local"> (local "YYYY-MM-DDTHH:MM") from an
  // ISO string, or the current minute when none is given.
  function inputValue(iso) {
    const d = iso ? new Date(iso) : new Date();
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}` +
      `T${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function render() {
    const sim = state.is_simulated;
    const statusColor = sim ? AMBER : GREEN;
    const statusText = sim
      ? `Simulated: ${esc(SIM_FMT.format(new Date(state.simulated_time)))}`
      : "Using real time (Europe/London)";
    container.innerHTML = `
      <p style="font-size:12px;color:${DIM};margin:0;">Real time (Europe/London)</p>
      <div id="clk-real" style="font-size:20px;font-weight:700;color:${GREEN};margin:2px 0 10px;">—</div>
      <p style="font-weight:600;color:${statusColor};margin:0 0 10px;">${sim ? "◉" : "●"} ${statusText}</p>
      <input type="datetime-local" id="clk-input" value="${inputValue(state.simulated_time)}"
        style="width:100%;box-sizing:border-box;background:#0a0f1a;color:#cfe;border:1px solid #244;
               border-radius:6px;padding:8px;font-family:inherit;margin-bottom:10px;">
      <div style="display:flex;gap:8px;flex-wrap:wrap;">
        <button id="clk-set" style="flex:1;min-width:110px;background:#123;color:${AMBER};
          border:1px solid ${AMBER};border-radius:6px;padding:8px;cursor:pointer;font-family:inherit;">Set Clock</button>
        <button id="clk-reset" style="flex:1;min-width:110px;background:#123;color:${GREEN};
          border:1px solid ${GREEN};border-radius:6px;padding:8px;cursor:pointer;font-family:inherit;">Reset to Now</button>
      </div>
      <p id="clk-msg" style="font-size:12px;color:${DIM};min-height:1em;margin:8px 0 0;"></p>
    `;

    document.getElementById("clk-set").onclick = onSet;
    document.getElementById("clk-reset").onclick = onReset;

    if (realTimer) clearInterval(realTimer);
    const tick = () => {
      const el = document.getElementById("clk-real");
      if (el) el.textContent = REAL_FMT.format(new Date());
    };
    tick();
    realTimer = setInterval(tick, 1000);
  }

  function msg(text, color) {
    const el = document.getElementById("clk-msg");
    if (el) { el.textContent = text; el.style.color = color || DIM; }
  }

  async function onSet() {
    const val = document.getElementById("clk-input").value;
    if (!val) { msg("Pick a date and time first.", AMBER); return; }
    msg("Setting…");
    try {
      const r = await apiPost("/api/settings/simulated-time", { dt: val });
      state = r;
      render();
      msg("Clock set — new logs will backfill here.", AMBER);
    } catch (e) { msg("Failed: " + esc(e.message || e), "#ff4455"); }
  }

  async function onReset() {
    msg("Resetting…");
    try {
      const r = await apiPost("/api/settings/simulated-time", { dt: null });
      state = r;
      render();
      msg("Back on real time.", GREEN);
    } catch (e) { msg("Failed: " + esc(e.message || e), "#ff4455"); }
  }

  apiGet("/api/settings/current-time")
    .then((r) => { state = r; render(); })
    .catch((e) => {
      container.innerHTML = `<p style="color:#ff4455;">Error: ${esc(e.message || e)}</p>`;
    });
})();
